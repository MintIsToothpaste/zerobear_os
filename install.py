#!/usr/bin/env python3
"""Install OS skills and common instructions, backing up all replaced items."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
from scripts.install_support import (
    NAME, create_link, digest, directory_snapshot, installation_lock, linked, load_state, owned,
    present, remove_item, safe_path, save_state, state_directory,
)


def apply_plans(home, root, plans, instruction_bytes, dry_run):
    with installation_lock(home, dry_run):
        state = load_state(home)
        state["source_root"] = str(root)
        for relative, planned_item in plans:
            item = planned_item.copy()
            path = safe_path(home, relative)
            previous = state["items"].get(relative)
            backup = None
            if present(path):
                managed = previous is not None and owned(path, previous)
                if managed and all(previous.get(key) == value for key, value in item.items()):
                    print(f"UNCHANGED: {path}")
                    continue
                print(f"{'WOULD REPLACE (with backup)' if dry_run else 'REPLACE (with backup)'}: {path}")
                if not dry_run:
                    backup_root = state_directory(home) / "backups"
                    if linked(backup_root):
                        raise ValueError(f"Linked backup directory is not supported: {backup_root}")
                    backup_root.mkdir(exist_ok=True)
                    backup = backup_root / uuid.uuid4().hex
                    backup.mkdir()
                    backup = backup / path.name
                    # Move only the checked installation entry; never traverse its contents.
                    path.rename(backup)
                    print(f"BACKUP: {backup}")
            else:
                print(f"{'WOULD INSTALL' if dry_run else 'INSTALL'}: {path}")
            if dry_run:
                continue
            try:
                if item["kind"] == "link":
                    create_link(path, Path(item["target"]))
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        stream.write(instruction_bytes)
                if previous and previous.get("backups"):
                    item["backups"] = list(previous["backups"])
                if backup is not None:
                    item.setdefault("backups", []).append(str(backup))
                state["items"][relative] = item
                save_state(home, state)
            except Exception:
                if present(path) and (owned(path, item) or (item["kind"] == "directory" and not linked(path))):
                    remove_item(home, relative, item)
                if backup is not None and not present(path):
                    backup.rename(path)
                raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(), help="User home (default: current user)")
    parser.add_argument("--runtime", choices=("all", "codex", "claude"), default="all")
    parser.add_argument("--scope", choices=("all", "user", "project"), default="all", help="Install user-level and/or OS-local skill links")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing anything")
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    root = Path(__file__).resolve().parent
    runtimes = []
    instruction_paths = []
    if args.runtime in ("all", "codex"):
        runtimes.extend((".agents/skills", ".codex/skills"))
        instruction_paths.append(".codex/AGENTS.md")
    if args.runtime in ("all", "claude"):
        runtimes.append(".claude/skills")
        instruction_paths.append(".claude/CLAUDE.md")

    plans = []
    for skill in sorted((root / "skills").iterdir()):
        if skill.is_dir() and (skill / "SKILL.md").is_file():
            if not NAME.fullmatch(skill.name):
                raise ValueError(f"Invalid skill folder name: {skill.name}")
            if not skill.resolve().is_relative_to((root / "skills").resolve()):
                raise ValueError(f"Skill target is outside the OS skills directory: {skill}")
            for runtime in runtimes:
                relative = f"{runtime}/{skill.name}"
                directory_snapshot(skill)
                plans.append((relative, {"kind": "link", "target": str(skill.resolve())}))
    instructions = root / "AGENTS.md"
    instruction_bytes = instructions.read_bytes()
    for relative in instruction_paths:
        plans.append((relative, {"kind": "file", "sha256": digest(instructions)}))

    outcomes = []
    if args.scope in ("all", "user"):
        outcomes.append(apply_plans(home, root, plans, instruction_bytes, args.dry_run))
    if args.scope in ("all", "project") and (args.scope == "project" or root != home):
        local_plans = [(relative, item.copy()) for relative, item in plans if item["kind"] == "link"]
        outcomes.append(apply_plans(root, root, local_plans, instruction_bytes, args.dry_run))
    return max(outcomes, default=0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
