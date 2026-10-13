"""Synthetic session recording and hook-installation tests; no live transcripts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hooks.session_close import (
    configure, handle_hook, read_json, run_action, state_paths, token_display,
    transcript_metadata, valid_receipt, write_json,
)
from scripts.session_hook_install import handler_definition, install_hooks, uninstall_hooks


class SessionCloseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".session-test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "한글 home"
        self.sid = "test-session-001"
        self.vault = Path(self.temporary.name) / "한글 세션 폴더"

    def event(self, runtime="codex", kind="UserPromptSubmit", sid=None, **extra):
        return handle_hook(self.home, runtime, {"session_id": sid or self.sid,
            "hook_event_name": kind, "cwd": str(Path(self.temporary.name) / "프로젝트"), **extra})

    def action(self, action, runtime="codex", sid=None, **extra):
        return run_action(argparse.Namespace(home=self.home, runtime=runtime, session_id=sid or self.sid,
            action=action, vault=str(self.vault), create=False, input=None, **extra))

    def payload(self, title="세션 기록 훅 구현"):
        return {"title": title, "summary": "세션 기록 기능 구현과 합성 테스트 완료",
            "sections": {"goal_scope": ["요청한 기록 기능만 구현"], "progress": ["기록 코드 작성"],
                "verification": ["실제 앱 동작은 미검증"], "next_steps": ["실제 대화에서 검증"]}}

    def begin_ready(self, runtime="codex", sid=None):
        self.event(runtime, sid=sid)
        self.vault.mkdir(parents=True, exist_ok=True)
        configure(self.home, str(self.vault), False)
        return self.action("begin", runtime, sid)

    def save(self, ready, runtime="codex", sid=None, payload=None):
        file = Path(ready["input_file"])
        file.write_text(json.dumps(payload or self.payload(), ensure_ascii=False), encoding="utf-8")
        args = argparse.Namespace(home=self.home, runtime=runtime, session_id=sid or self.sid,
            action="save", vault=None, create=False, input=file)
        return run_action(args)

    def test_missing_path_waits_then_continues_without_another_close_request(self):
        context = self.event()
        self.assertIn("session-close", context["hookSpecificOutput"]["additionalContext"])
        waiting = self.action("begin")
        self.assertEqual(waiting["status"], "needs_path")
        self.assertEqual(self.event(kind="Stop"), {})
        outcome = configure(self.home, str(self.vault), False)
        self.assertEqual(outcome["status"], "needs_creation_confirmation")
        self.assertFalse(self.vault.exists())
        configure(self.home, str(self.vault), True)
        self.event(prompt=str(self.vault))
        ready = self.action("begin")
        saved = self.save(ready)
        self.assertTrue(saved["verified"])
        self.assertEqual(self.event(kind="Stop"), {})

    def test_ordinary_prompts_never_arm_recording(self):
        for prompt in ("앱 종료 버그 분석해줘", "아직 종료하지 마", "종료하면 기록돼?", "일반 개발 작업"):
            self.event(prompt=prompt)
            self.assertEqual(self.event(kind="Stop"), {})
        # Semantic decisions belong to the current agent, not a keyword matcher.
        _, path = state_paths(self.home, "codex", self.sid)
        self.assertNotIn("request_id", read_json(path))

    def test_both_runtimes_write_natural_title_and_no_cost(self):
        for runtime in ("claude", "codex"):
            ready = self.begin_ready(runtime)
            saved = self.save(ready, runtime)
            path = Path(saved["note_path"])
            self.assertIn("프로젝트-세션 기록 훅 구현", path.name)
            text = path.read_text(encoding="utf-8")
            self.assertIn("started_at:", text)
            for key in ("created", "recorded_at", "tokens_observed_at", "session_id"):
                self.assertNotIn(f"\n{key}:", text)
            self.assertNotIn("cost_usd", text)
            self.assertNotIn("tokens_total:", text)
            self.assertNotIn("\n# ", text)
            self.assertNotIn("토큰:", text)
            self.assertEqual(self.event(runtime, "Stop"), {})

    def test_same_session_updates_and_title_collisions_preserve_other_notes(self):
        ready = self.begin_ready()
        first = self.save(ready)
        ready = self.action("begin")
        payload = self.payload("다른 작업 제목")
        payload["sections"]["progress"].append("추가 작업 완료")
        second = self.save(ready, payload=payload)
        self.assertEqual(first["note_path"], second["note_path"])
        self.assertIn("추가 작업 완료", Path(second["note_path"]).read_text(encoding="utf-8"))
        third_ready = self.begin_ready(sid="another-session")
        third = self.save(third_ready, sid="another-session")
        self.assertNotEqual(first["note_path"], third["note_path"])
        self.assertEqual(len(list(self.vault.glob("*.md"))), 2)

    def test_save_failure_does_not_complete_and_stop_retries_only_once(self):
        ready = self.begin_ready()
        bad = self.payload()
        bad["cost_usd"] = 10
        with self.assertRaises(ValueError):
            self.save(ready, payload=bad)
        _, path = state_paths(self.home, "codex", self.sid)
        state = read_json(path)
        self.assertEqual(state["stage"], "failed")
        self.assertFalse(valid_receipt(state, "codex", self.sid))
        self.assertEqual(self.event(kind="Stop")["decision"], "block")
        self.assertNotIn("decision", self.event(kind="Stop", stop_hook_active=True))

    def test_deleted_or_modified_saved_note_is_not_accepted_as_verified(self):
        ready = self.begin_ready()
        saved = self.save(ready)
        Path(saved["note_path"]).write_text("外部 변경", encoding="utf-8")
        self.assertEqual(self.event(kind="Stop")["decision"], "block")
        ready = self.action("begin")
        with self.assertRaises(ValueError):
            self.save(ready)
        self.assertEqual(Path(saved["note_path"]).read_text(encoding="utf-8"), "外部 변경")

    def test_cancel_and_unknown_context_do_not_record(self):
        with self.assertRaises(ValueError):
            self.action("begin")
        self.event()
        self.action("begin")
        self.action("cancel")
        self.assertEqual(self.event(kind="Stop"), {})
        self.assertFalse(self.vault.exists())

    def test_later_normal_work_does_not_rearm_a_completed_request(self):
        ready = self.begin_ready()
        saved = self.save(ready)
        self.event(prompt="다음 기능을 분석해줘")
        Path(saved["note_path"]).unlink()
        self.assertEqual(self.event(kind="Stop"), {})

    def test_token_formats_and_claude_message_deduplication(self):
        self.assertEqual([token_display(n) for n in (850, 12345, 1234567)], ["850", "12.35K", "1.23M"])
        transcript = Path(self.temporary.name) / "claude.jsonl"
        usage = {"input_tokens": 1000, "output_tokens": 30, "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 100}
        record = {"type": "assistant", "sessionId": self.sid, "timestamp": "2026-10-13T09:00:00Z",
            "message": {"id": "message-1", "model": "synthetic-model", "usage": usage}}
        transcript.write_text((json.dumps(record) + "\n") * 2, encoding="utf-8")
        meta = transcript_metadata(str(transcript), "claude", self.sid)
        self.assertEqual(meta["tokens_total"], 1630)
        self.assertEqual(meta["tokens_display"], "1.63K")
        self.assertEqual(transcript_metadata(str(transcript), "claude", "wrong-session"), {})

    def test_codex_uses_cumulative_count_not_sum_and_unknown_format_omits_tokens(self):
        transcript = Path(self.temporary.name) / "codex.jsonl"
        records = [{"type": "session_meta", "payload": {"id": self.sid}}]
        for value in (1000, 1500):
            records.append({"type": "event_msg", "payload": {"type": "token_count",
                "info": {"total_token_usage": {"total_tokens": value}}}})
        transcript.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
        self.assertEqual(transcript_metadata(str(transcript), "codex", self.sid)["tokens_total"], 1500)
        transcript.write_text('{"unexpected": true}\n', encoding="utf-8")
        self.assertNotIn("tokens_total", transcript_metadata(str(transcript), "codex", self.sid))

    def test_compact_properties_with_known_tokens_and_legacy_note_update(self):
        ready = self.begin_ready()
        transcript = Path(self.temporary.name) / "codex.jsonl"
        records = [
            {"type": "session_meta", "timestamp": "2026-10-13T09:00:00Z", "payload": {"id": self.sid}},
            {"type": "event_msg", "payload": {"type": "token_count",
                "info": {"total_token_usage": {"total_tokens": 12345}}}},
        ]
        transcript.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
        self.event(transcript_path=str(transcript))
        first = self.save(ready)
        note = Path(first["note_path"])
        text = note.read_text(encoding="utf-8")
        self.assertIn('started_at: "2026-10-13T09:00:00Z"', text)
        self.assertIn('tokens_display: "12.35K"', text)
        self.assertNotIn("\n# ", text)
        self.assertNotIn("토큰:", text)
        for key in ("tokens_total", "tokens_observed_at", "created", "recorded_at", "session_id"):
            self.assertNotIn(f"\n{key}:", text)
        self.assertEqual(self.event(kind="Stop"), {})
        # Existing notes from the previous format still update in place.
        note.write_text('---\nagent: "codex"\nsession_id: "' + self.sid + '"\n---\nLegacy note\n', encoding="utf-8")
        updated = self.save(self.action("begin"))
        self.assertEqual(updated["note_path"], first["note_path"])
        self.assertNotIn("session_id:", note.read_text(encoding="utf-8"))
        self.assertEqual(self.event(kind="Stop"), {})

    def test_hook_command_executes_with_korean_paths_and_outputs_valid_json(self):
        for runtime in ("codex", "claude"):
            handler = handler_definition(self.home, ROOT, runtime)
            command = handler.get("commandWindows", handler["command"]) if os.name == "nt" else handler["command"]
            event = {"hook_event_name": "UserPromptSubmit", "session_id": self.sid,
                "cwd": str(self.vault), "prompt": "오늘은 여기까지 하자"}
            result = subprocess.run(command, shell=True, input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            self.assertIn("additionalContext", json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"])


class HookInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".hook-test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def test_merge_replace_without_backup_and_remove_only_owned_hooks(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir()
        legacy = {"type": "command", "command": "powershell", "args": ["-File", str(self.home / ".claude/hooks/record-session.ps1")]}
        other = {"type": "command", "command": "another-stop-handler"}
        config = {"autoUpdatesChannel": "latest", "hooks": {"Stop": [{"hooks": [legacy, other]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "other-start"}]}]}}
        data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8")
        path.write_bytes(b"\xef\xbb\xbf" + data)
        install_hooks(self.home, ROOT, "all")
        after = read_json(path)
        handlers = [entry for group in after["hooks"]["Stop"] for entry in group["hooks"]]
        self.assertIn(other, handlers)
        self.assertNotIn(legacy, handlers)
        self.assertEqual(len(handlers), 2)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", path.read_bytes())
        self.assertFalse((self.home / ".zerobear-os/backups").exists())
        install_hooks(self.home, ROOT, "all")
        self.assertEqual(after, read_json(path))
        uninstall_hooks(self.home, "all")
        remaining = read_json(path)
        self.assertEqual(remaining["hooks"]["Stop"], [{"hooks": [other]}])
        self.assertEqual(remaining["hooks"]["SessionStart"], config["hooks"]["SessionStart"])
        self.assertEqual(remaining["autoUpdatesChannel"], "latest")

    def test_dry_run_does_not_write_any_configuration(self):
        install_hooks(self.home, ROOT, "all", dry_run=True)
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".zerobear-os").exists())


if __name__ == "__main__":
    unittest.main()
