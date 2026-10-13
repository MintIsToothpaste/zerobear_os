"""Merge owned session-recording hooks without replacing unrelated settings."""

from __future__ import annotations

import base64
import json
import os
import shlex
import sys
from pathlib import Path

from scripts.install_support import installation_lock, linked, load_state, save_state

OWNER = "zerobear-session-close"
CONFIGS = {"codex": ".codex/hooks.json", "claude": ".claude/settings.json"}


def config_path(home, runtime):
    path = home / CONFIGS[runtime]
    if linked(path) or linked(path.parent):
        raise ValueError(f"Linked hook configuration is not supported: {path}")
    return path


def read_config(path):
    if not path.exists():
        return {}, None
    original = path.read_bytes()
    config = json.loads(original.decode("utf-8-sig"))
    if not isinstance(config, dict) or not isinstance(config.get("hooks", {}), dict):
        raise ValueError(f"Invalid hooks configuration: {path}")
    return config, original


def write_config(path, config, original):
    from hooks.session_close import atomic_bytes
    newline = "\r\n" if original and b"\r\n" in original else "\n"
    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    content = text.replace("\n", newline).encode("utf-8")
    if original and original.startswith(b"\xef\xbb\xbf"):
        content = b"\xef\xbb\xbf" + content
    atomic_bytes(path, content)


def owned_handler(handler):
    return isinstance(handler, dict) and (
        handler.get("statusMessage") == OWNER
        or "--hook-owner " + OWNER in str(handler.get("command", ""))
    )


def legacy_handler(handler, home):
    if not isinstance(handler, dict):
        return False
    wanted = str(home / ".claude/hooks/record-session.ps1").replace("\\", "/").lower()
    arguments = handler.get("args", [])
    if not isinstance(arguments, list):
        arguments = []
    values = [str(handler.get("command", "")), *map(str, arguments)]
    return any(wanted in value.replace("\\", "/").lower() for value in values)


def strip_handlers(groups, predicate):
    if not isinstance(groups, list):
        raise ValueError("Hook event groups must be arrays.")
    result = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ValueError("Malformed hook group; left unchanged.")
        remaining = [handler for handler in group["hooks"] if not predicate(handler)]
        if remaining:
            result.append({**group, "hooks": remaining})
    return result


def handler_definition(home, root, runtime):
    argv = [sys.executable, "-B", str(root / "hooks/session_close.py"), "hook",
            "--runtime", runtime, "--home", str(home), "--hook-owner", OWNER]
    portable = " ".join(shlex.quote(argument) for argument in argv)
    handler = {"type": "command", "command": portable, "timeout": 15, "statusMessage": OWNER}
    if os.name == "nt":
        script = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        script += "$OutputEncoding = [Console]::OutputEncoding; & "
        script += " ".join("'" + argument.replace("'", "''") + "'" for argument in argv)
        script += "; exit $LASTEXITCODE"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        windows = "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded
        if runtime == "claude":
            handler["command"] = windows
        else:
            handler["commandWindows"] = windows
    return handler


def install_hooks(home, root, runtime_filter, dry_run=False):
    runtimes = list(CONFIGS) if runtime_filter == "all" else [runtime_filter]
    with installation_lock(home, dry_run):
        state = load_state(home)
        for runtime in runtimes:
            path = config_path(home, runtime)
            config, original = read_config(path)
            hooks = config.setdefault("hooks", {})
            handler = handler_definition(home, root, runtime)
            for event in ("UserPromptSubmit", "Stop"):
                predicate = lambda entry: owned_handler(entry) or (runtime == "claude" and event == "Stop" and legacy_handler(entry, home))
                groups = strip_handlers(hooks.get(event, []), predicate)
                groups.append({"hooks": [handler.copy()]})
                hooks[event] = groups
            print(f"{'WOULD CONFIGURE' if dry_run else 'CONFIGURE'} session hooks: {path}")
            if dry_run:
                continue
            # Legacy recorder registration is replaced without a backup, as requested.
            write_config(path, config, original)
            state.setdefault("hook_configs", {})[runtime] = CONFIGS[runtime]
            try:
                save_state(home, state)
            except Exception:
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)
                raise
    if "codex" in runtimes:
        print("Codex: open /hooks in a new local session and review/trust the new hooks before using session recording.")


def uninstall_hooks(home, runtime_filter, dry_run=False):
    with installation_lock(home, dry_run):
        state = load_state(home)
        recorded = state.get("hook_configs", {})
        for runtime in list(recorded):
            if runtime not in CONFIGS or runtime_filter not in ("all", runtime):
                continue
            path = config_path(home, runtime)
            if path.exists():
                config, original = read_config(path)
                hooks = config.get("hooks", {})
                for event in ("UserPromptSubmit", "Stop"):
                    if event in hooks:
                        groups = strip_handlers(hooks[event], owned_handler)
                        if groups:
                            hooks[event] = groups
                        else:
                            del hooks[event]
                if not hooks:
                    config.pop("hooks", None)
                print(f"{'WOULD REMOVE' if dry_run else 'REMOVE'} session hooks: {path}")
                if not dry_run:
                    write_config(path, config, original)
            if not dry_run:
                del recorded[runtime]
                save_state(home, state)
