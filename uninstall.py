#!/usr/bin/env python3
"""Remove only unmodified items recorded by the OS installer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from scripts.install_support import (
    installation_lock, load_state, owned, present, remove_item,
    safe_path, save_state, state_directory,
)
from scripts.session_hook_install import uninstall_hooks


def remove_scope(home, runtime_filter, dry_run):
    if not (state_directory(home) / "install.json").exists():
        print("Nothing recorded to uninstall.")
        return 0

    conflicts = 0
    with installation_lock(home, dry_run):
        state = load_state(home)
        for relative, item in list(state["items"].items()):
            runtime = "claude" if relative.startswith(".claude/") else "codex"
            if runtime_filter not in ("all", runtime):
                continue
            path = safe_path(home, relative)
            if not present(path):
                print(f"ALREADY ABSENT: {path}")
            elif not owned(path, item):
                print(f"MODIFIED (left unchanged): {path}")
                conflicts += 1
                continue
            else:
                print(f"{'WOULD REMOVE' if dry_run else 'REMOVE'}: {path}")
                if not dry_run:
                    remove_item(home, relative, item)
            if not dry_run:
                del state["items"][relative]
                save_state(home, state)
    return 2 if conflicts else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(), help="User home (default: current user)")
    parser.add_argument("--runtime", choices=("all", "codex", "claude"), default="all")
    parser.add_argument("--scope", choices=("all", "user", "project"), default="all", help="Remove user-level and/or OS-local installed items")
    parser.add_argument("--dry-run", action="store_true", help="Preview without removing anything")
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    root = Path(__file__).resolve().parent
    outcomes = []
    if args.scope in ("all", "user"):
        if (state_directory(home) / "install.json").exists():
            uninstall_hooks(home, args.runtime, args.dry_run)
        outcomes.append(remove_scope(home, args.runtime, args.dry_run))
    if args.scope in ("all", "project") and (args.scope == "project" or root != home):
        outcomes.append(remove_scope(root, args.runtime, args.dry_run))
    return max(outcomes, default=0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
