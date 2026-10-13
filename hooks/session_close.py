#!/usr/bin/env python3
"""Session-close hook and recording CLI shared by Codex and Claude Code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.install_support import linked, state_directory

OWNER = "zerobear-session-close"
SECTION_NAMES = {
    "goal_scope": "목표와 범위", "progress": "진행 결과", "changed_files": "변경 파일",
    "decisions": "결정 사항", "verification": "검증 결과", "issues": "남은 문제",
    "next_steps": "다음 할 일", "links": "관련 자료",
}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path):
    if linked(path):
        raise ValueError(f"Linked state file is not supported: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def atomic_bytes(path, content, exclusive=False):
    if linked(path):
        raise ValueError(f"Refusing to replace a linked file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".session-close-", dir=path.parent)
    temporary = Path(temporary)
    reserved = False
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # Reserve the name without overwriting a concurrently-created note.
            with path.open("xb"):
                pass
            reserved = True
        os.replace(temporary, path)
        if path.read_bytes() != content:
            raise OSError(f"Saved content verification failed: {path}")
    except OSError:
        if reserved and path.is_file() and not linked(path) and path.stat().st_size == 0:
            path.unlink()
        raise
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, data):
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def state_paths(home, runtime, sid):
    if not sid or len(sid) > 300:
        raise ValueError("A real session ID from the hook is required.")
    directory = state_directory(home) / "session-recording"
    key = hashlib.sha256((runtime + ":" + sid).encode()).hexdigest()[:24]
    sessions = directory / "sessions"
    for path in (directory, sessions):
        if linked(path):
            raise ValueError(f"Linked state directory is not supported: {path}")
    return directory / "config.json", sessions / (key + ".json")


@contextmanager
def session_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        with lock.open("x"):
            pass
    except FileExistsError as error:
        raise ValueError(f"Session recording is already running: {lock}") from error
    try:
        yield
    finally:
        lock.unlink()


def token_display(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return str(value)


def nonnegative(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def transcript_metadata(path, runtime, sid):
    """Read known usage shapes only; unknown/mismatched formats omit tokens."""
    result = {}
    if not path or not Path(path).is_file():
        return result
    messages = {}
    models = []
    first_timestamp = None
    codex_total = None
    wrong_session = False
    incomplete_usage = False
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                timestamp = record.get("timestamp")
                if first_timestamp is None and isinstance(timestamp, str):
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        first_timestamp = timestamp
                    except ValueError:
                        pass
                if runtime == "codex":
                    payload = record.get("payload", {})
                    if not isinstance(payload, dict):
                        continue
                    if record.get("type") == "session_meta" and payload.get("id") != sid:
                        wrong_session = True
                    if record.get("type") == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info") or {}
                        if not isinstance(info, dict):
                            continue
                        usage = info.get("total_token_usage") or {}
                        if not isinstance(usage, dict):
                            continue
                        if nonnegative(usage.get("total_tokens")):
                            codex_total = usage["total_tokens"]
                else:
                    if record.get("sessionId") and record["sessionId"] != sid:
                        wrong_session = True
                    message = record.get("message", {})
                    if record.get("type") != "assistant" or not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    identity = message.get("id")
                    if not identity or not nonnegative(usage.get("input_tokens")) or not nonnegative(usage.get("output_tokens")):
                        incomplete_usage = True
                        continue
                    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                    values = [usage.get(field, 0) for field in fields]
                    if not all(nonnegative(value) for value in values):
                        incomplete_usage = True
                        continue
                    previous = messages.get(identity, [0] * len(fields))
                    messages[identity] = [max(old, new) for old, new in zip(previous, values)]
                    model = message.get("model")
                    if isinstance(model, str) and model not in models:
                        models.append(model)
    except (OSError, UnicodeError):
        return {}
    if wrong_session:
        return {}
    if first_timestamp:
        result["started_at"] = first_timestamp
    total = codex_total if runtime == "codex" else (sum(map(sum, messages.values())) if messages and not incomplete_usage else None)
    if total is not None:
        result["tokens_total"] = total
        result["tokens_display"] = token_display(total)
        result["tokens_observed_at"] = now()
    if models:
        result["models"] = models
    return result


def safe_name(text):
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")[:70].strip(" .")
    if not text:
        raise ValueError("Project and work title must not be empty.")
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", text):
        text = "_" + text
    return text


def same_note(path, runtime, sid, state=None):
    if not path.is_file() or linked(path):
        return False
    # New notes keep identity in local state rather than visible properties.
    if state and state.get("note_path") and path.resolve() == Path(state["note_path"]).resolve():
        if hashlib.sha256(path.read_bytes()).hexdigest() == state.get("note_sha256"):
            return True
    # Accept older notes so the next save migrates their properties.
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return False
    lines = text.splitlines()
    try:
        frontmatter = lines[1:lines.index("---", 1)]
    except ValueError:
        return False
    return f"agent: {json.dumps(runtime)}" in frontmatter and f"session_id: {json.dumps(sid)}" in frontmatter


def note_path(vault, state, project, title, runtime, sid):
    if state.get("note_path"):
        previous = Path(state["note_path"])
        if previous.parent.resolve() != vault.resolve():
            raise ValueError("Existing session note belongs to another folder; keep its configured folder for this session.")
        if previous.exists() and not same_note(previous, runtime, sid, state):
            raise ValueError("The existing session note identity changed; it will not be overwritten.")
        return previous
    stem = safe_name(project) + "-" + safe_name(title)
    path = vault / (stem + ".md")
    if not path.exists() or same_note(path, runtime, sid):
        return path
    suffix = hashlib.sha256((runtime + sid).encode()).hexdigest()[:8]
    path = vault / (stem + "-" + suffix + ".md")
    if path.exists() and not same_note(path, runtime, sid):
        raise ValueError("The alternate session filename is already in use.")
    return path


def render_note(payload, state, runtime, sid):
    if set(payload) - {"title", "summary", "sections"}:
        raise ValueError("Only title, summary and sections are accepted; metadata is collected by the recorder.")
    title, summary = payload.get("title"), payload.get("summary")
    if not isinstance(title, str) or not title.strip() or not isinstance(summary, str) or not summary.strip():
        raise ValueError("A work title and one-line summary are required.")
    if "\n" in title or "\n" in summary or len(title) > 120 or len(summary) > 500:
        raise ValueError("Keep the title and summary short and on one line.")
    sections = payload.get("sections")
    if not isinstance(sections, dict) or set(sections) - set(SECTION_NAMES):
        raise ValueError("Use the documented session section keys.")
    if not sections.get("progress") or not sections.get("next_steps"):
        raise ValueError("Progress and next steps are required; state explicitly when no next task remains.")
    metadata = transcript_metadata(state.get("transcript_path"), runtime, sid)
    fields = {"type": "session", "project": state["project"], "agent": runtime,
              "started_at": metadata.get("started_at") or state.get("created") or now(),
              "summary": summary, "tags": ["session"]}
    for key in ("tokens_display", "models"):
        if key in metadata:
            fields[key] = metadata[key]
    if isinstance(state.get("model"), str) and state["model"]:
        fields["model"] = state["model"]
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()] + ["---", ""]
    for key, heading in SECTION_NAMES.items():
        entries = sections.get(key, [])
        if not isinstance(entries, list) or not all(isinstance(entry, str) and entry.strip() for entry in entries):
            raise ValueError(f"Section {key} must be a list of non-empty strings.")
        if entries:
            lines += [f"## {heading}", ""]
            prefix = "- [ ] " if key == "next_steps" else "- "
            lines += [prefix + entry.replace("\n", "\n  ") for entry in entries] + [""]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8"), title


def command_hint(runtime, sid, home, action):
    arguments = [sys.executable, "-B", str(Path(__file__).resolve()), action,
                 "--runtime", runtime, "--session-id", sid, "--home", str(home)]
    if os.name == "nt":
        return "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)
    return " ".join(shlex.quote(argument) for argument in arguments)


def valid_receipt(state, runtime, sid):
    if state.get("stage") != "complete" or state.get("completed_request") != state.get("request_id"):
        return False
    path = Path(state.get("note_path", ""))
    return same_note(path, runtime, sid, state) and hashlib.sha256(path.read_bytes()).hexdigest() == state.get("note_sha256")


def handle_hook(home, runtime, event):
    sid = event.get("session_id")
    if not isinstance(sid, str) or not sid or event.get("agent_id"):
        return {}
    _, path = state_paths(home, runtime, sid)
    with session_lock(path):
        state = read_json(path)
        kind = event.get("hook_event_name")
        if kind == "UserPromptSubmit":
            if state.get("stage") == "complete":
                # A later ordinary prompt must not re-arm a completed close request.
                state["stage"] = "inactive"
            state.setdefault("created", now())
            state["cwd"] = event.get("cwd") or state.get("cwd") or str(ROOT)
            state.setdefault("project", Path(state["cwd"]).name or "project")
            if event.get("transcript_path"):
                state["transcript_path"] = event["transcript_path"]
            if event.get("model"):
                state["model"] = event["model"]
            write_json(path, state)
            context = (
                "Session recording: decide the current human user's intent from context, not exact words. "
                "Only for an actual request to finish this chat or save this session, use the session-close skill. "
                "App-exit bugs, questions, quotes and negated/cancelled requests are not close requests. "
                f"Start/status command: {command_hint(runtime, sid, home, 'begin')}. "
                f"Current recording stage: {state.get('stage', 'inactive')}. "
                "If waiting for a folder, treat the user's path answer as continuation; do not require another close prompt. "
                "Do not claim a saved note until the recording command returns success."
            )
            return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
        if kind != "Stop" or state.get("stage") in (None, "inactive", "waiting_path", "cancelled"):
            return {}
        if valid_receipt(state, runtime, sid):
            return {}
        if not state.get("stop_nudged") and not event.get("stop_hook_active"):
            state["stop_nudged"] = True
            write_json(path, state)
            return {"decision": "block", "reason": "The requested session record has no verified saved note. Follow session-close to save it, ask for the folder if needed, or report the exact failure. Do not claim completion or close the app."}
        return {"systemMessage": "세션 기록이 저장되지 않았거나 저장 확인에 실패했습니다. 기록 완료로 처리하지 않았습니다."}


def configure(home, directory, create):
    path = Path(directory).expanduser()
    if not path.is_absolute():
        raise ValueError("Provide an absolute sessions folder path.")
    if not path.exists():
        if not create:
            return {"status": "needs_creation_confirmation", "path": str(path)}
        path.mkdir(parents=True)
    if not path.is_dir():
        raise ValueError("The sessions path must be a directory.")
    path = path.resolve()
    # Test a real write; permission bits alone are not sufficient.
    handle, probe = tempfile.mkstemp(prefix=".zerobear-write-check-", dir=path)
    os.close(handle)
    Path(probe).unlink()
    config, _ = state_paths(home, "codex", "configuration")
    write_json(config, {"sessions_dir": str(path)})
    return {"status": "configured", "sessions_dir": str(path)}


def run_action(args):
    home = args.home.expanduser().resolve()
    config_path, path = state_paths(home, args.runtime, args.session_id)
    if args.action == "configure":
        return configure(home, args.vault, args.create)
    with session_lock(path):
        state = read_json(path)
        if args.action == "cancel":
            state["stage"] = "cancelled"
            write_json(path, state)
            return {"status": "cancelled", "recorded": False}
        if not state.get("cwd"):
            raise ValueError("Session context is missing. Start a new chat after installing/trusting the hooks; do not invent a session ID.")
        if args.action == "begin":
            if state.get("stage") not in ("pending", "waiting_path", "failed"):
                state.update(request_id=uuid.uuid4().hex, stop_nudged=False)
            config = read_json(config_path)
            vault = config.get("sessions_dir")
            if not vault or not Path(vault).is_dir():
                state["stage"] = "waiting_path"
                write_json(path, state)
                return {"status": "needs_path", "message": "세션 기록을 저장할 폴더의 절대 경로를 알려주세요.",
                        "configure_command": command_hint(args.runtime, args.session_id, home, "configure") + " --vault <folder> [--create]"}
            state["stage"] = "pending"
            state["vault"] = str(Path(vault).resolve())
            write_json(path, state)
            return {"status": "ready", "request_id": state["request_id"], "sessions_dir": state["vault"],
                    "project": state["project"], "input_file": str(path.with_suffix(".input.json")),
                    "save_command": command_hint(args.runtime, args.session_id, home, "save") + " --input <input_file>"}
        if args.action != "save" or state.get("stage") not in ("pending", "failed"):
            raise ValueError("Begin the requested recording before saving.")
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("The summary input must be a JSON object.")
            content, title = render_note(payload, state, args.runtime, args.session_id)
            vault = Path(state["vault"])
            if not vault.is_dir():
                raise ValueError("The configured folder disappeared; choose it again.")
            destination = note_path(vault, state, state["project"], title, args.runtime, args.session_id)
            atomic_bytes(destination, content, exclusive=not destination.exists())
            state.update(stage="complete", note_path=str(destination),
                         note_sha256=hashlib.sha256(content).hexdigest(), completed_request=state["request_id"])
            write_json(path, state)
            # Only remove the documented staging file, never an arbitrary input.
            if args.input.absolute() == path.with_suffix(".input.json").absolute():
                args.input.unlink(missing_ok=True)
            return {"status": "saved", "note_path": str(destination), "verified": True}
        except (OSError, ValueError, KeyError) as error:
            state.update(stage="failed", error=str(error))
            write_json(path, state)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("hook", "begin", "configure", "save", "cancel"))
    parser.add_argument("--runtime", choices=("codex", "claude"), required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--session-id")
    parser.add_argument("--vault")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--hook-owner", default=OWNER)
    args = parser.parse_args()
    try:
        if args.action == "hook":
            event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
            result = handle_hook(args.home.expanduser().resolve(), args.runtime, event)
        else:
            if args.action == "configure" and not args.vault:
                raise ValueError("--vault is required for configuration.")
            if args.action == "save" and args.input is None:
                raise ValueError("--input is required for saving.")
            result = run_action(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        if args.action == "hook":
            print(json.dumps({"systemMessage": f"세션 기록 훅 오류: {error}. 기록 완료로 처리하지 않았습니다."}, ensure_ascii=False))
            return 0
        print(json.dumps({"status": "error", "recorded": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8")
    raise SystemExit(main())
