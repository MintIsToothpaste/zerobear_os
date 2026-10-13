"""Exercise install/uninstall against disposable homes, never the user's home."""

from __future__ import annotations

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
from scripts.install_support import create_link, linked, remove_item, remove_link, save_state


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".installer-test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"

    def run_cli(self, script, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / script), "--home", str(self.home), "--scope", "user", *arguments],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def manifest(self):
        return json.loads((self.home / ".zerobear-os/install.json").read_text(encoding="utf-8"))

    def test_install_repeat_and_uninstall_preserve_sources(self):
        original = (ROOT / "skills/simple-word/SKILL.md").read_bytes()
        self.run_cli("install.py")
        before = self.manifest()
        skill_count = sum(1 for path in (ROOT / "skills").iterdir() if (path / "SKILL.md").is_file())
        self.assertEqual(len(before["items"]), 3 * skill_count + 2)
        for runtime in (".claude", ".codex", ".agents"):
            path = self.home / runtime / "skills/simple-word"
            self.assertTrue(linked(path))
            self.assertEqual(path.resolve(), ROOT / "skills/simple-word")
            self.assertEqual((path / "SKILL.md").read_bytes(), original)
        self.assertEqual((self.home / ".claude/CLAUDE.md").read_bytes(), (ROOT / "AGENTS.md").read_bytes())
        self.run_cli("install.py")
        self.assertEqual(self.manifest(), before)
        self.run_cli("uninstall.py")
        self.assertEqual(self.manifest()["items"], {})
        self.assertFalse((self.home / ".claude/skills/simple-word").exists())
        self.assertEqual((ROOT / "skills/simple-word/SKILL.md").read_bytes(), original)
        self.run_cli("uninstall.py")

    def test_dry_run_does_not_create_home(self):
        self.run_cli("install.py", "--dry-run")
        self.assertFalse(self.home.exists())
        self.run_cli("uninstall.py", "--dry-run")
        self.assertFalse(self.home.exists())

    def test_default_replaces_and_backs_up_conflicting_skills_and_instructions(self):
        old_skill = self.home / ".codex/skills/simple-word"
        old_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_bytes(b"existing skill\r\n")
        old_instructions = self.home / ".codex/AGENTS.md"
        old_instructions.write_bytes(b"existing instructions\r\n")
        self.run_cli("install.py")
        skill_record = self.manifest()["items"][".codex/skills/simple-word"]
        skill_backup = Path(skill_record["backups"][0])
        self.assertTrue(linked(old_skill))
        self.assertEqual(old_skill.resolve(), ROOT / "skills/simple-word")
        self.assertEqual((skill_backup / "SKILL.md").read_bytes(), b"existing skill\r\n")
        instruction_record = self.manifest()["items"][".codex/AGENTS.md"]
        backup = Path(instruction_record["backups"][0])
        self.assertEqual(old_instructions.read_bytes(), (ROOT / "AGENTS.md").read_bytes())
        self.assertEqual(backup.read_bytes(), b"existing instructions\r\n")
        self.run_cli("uninstall.py")
        self.assertFalse(old_skill.exists())
        self.assertEqual((skill_backup / "SKILL.md").read_bytes(), b"existing skill\r\n")
        self.assertFalse(old_instructions.exists())
        self.assertEqual(backup.read_bytes(), b"existing instructions\r\n")

    def test_modified_instruction_and_retargeted_link_are_preserved(self):
        self.run_cli("install.py")
        instruction = self.home / ".claude/CLAUDE.md"
        instruction.write_bytes(b"user changes\r\n")
        skill = self.home / ".claude/skills/simple-word"
        remove_link(skill)
        foreign = self.home / "foreign"
        foreign.mkdir()
        (foreign / "SKILL.md").write_bytes(b"foreign skill")
        create_link(skill, foreign)
        self.run_cli("uninstall.py", expected=2)
        self.assertEqual(instruction.read_bytes(), b"user changes\r\n")
        self.assertEqual(skill.resolve(), foreign)
        self.assertEqual(set(self.manifest()["items"]), {".claude/CLAUDE.md", ".claude/skills/simple-word"})

    def test_runtime_selection_and_uninstall_preview(self):
        self.run_cli("install.py", "--runtime", "claude")
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.home / ".agents").exists())
        before = self.manifest()
        self.run_cli("uninstall.py", "--dry-run")
        self.assertEqual(self.manifest(), before)
        self.assertTrue(linked(self.home / ".claude/skills/bug-debug"))
        self.assertTrue((self.home / ".claude/skills/bug-debug/SKILL.md").is_file())
        self.run_cli("uninstall.py", "--runtime", "codex")
        self.assertEqual(self.manifest(), before)
        self.run_cli("uninstall.py", "--runtime", "claude")
        self.assertEqual(self.manifest()["items"], {})

    def test_uninstall_refuses_linked_parent(self):
        self.run_cli("install.py", "--runtime", "claude")
        skills = self.home / ".claude/skills"
        for child in skills.iterdir():
            relative = child.relative_to(self.home).as_posix()
            remove_item(self.home, relative, self.manifest()["items"][relative])
        skills.rmdir()
        outside = self.home / "outside"
        outside.mkdir()
        protected = outside / "simple-word"
        create_link(protected, ROOT / "skills/simple-word")
        create_link(skills, outside)
        self.run_cli("uninstall.py", expected=1)
        self.assertTrue(linked(protected))

    def test_overwrite_backs_up_legacy_files_and_directories(self):
        skill = self.home / ".claude/skills/simple-word"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(b"legacy skill\r\n")
        instruction = self.home / ".claude/CLAUDE.md"
        instruction.write_bytes(b"legacy instructions\r\n")
        self.run_cli("install.py", "--runtime", "claude")
        state = self.manifest()
        skill_backup = Path(state["items"][".claude/skills/simple-word"]["backups"][0])
        instruction_backup = Path(state["items"][".claude/CLAUDE.md"]["backups"][0])
        self.assertEqual((skill_backup / "SKILL.md").read_bytes(), b"legacy skill\r\n")
        self.assertEqual(instruction_backup.read_bytes(), b"legacy instructions\r\n")
        self.assertTrue(linked(skill))
        self.assertEqual(instruction.read_bytes(), (ROOT / "AGENTS.md").read_bytes())
        self.run_cli("install.py", "--runtime", "claude")
        self.assertEqual(self.manifest(), state)
        instruction.write_bytes(b"edited after install")
        self.run_cli("install.py", "--runtime", "claude")
        backups = self.manifest()["items"][".claude/CLAUDE.md"]["backups"]
        self.assertEqual(len(backups), 2)
        self.assertEqual(Path(backups[-1]).read_bytes(), b"edited after install")
        self.run_cli("uninstall.py")
        self.assertFalse(instruction.exists())
        self.assertEqual(instruction_backup.read_bytes(), b"legacy instructions\r\n")
        self.assertEqual((skill_backup / "SKILL.md").read_bytes(), b"legacy skill\r\n")

    def test_default_preview_preserves_conflicting_instructions(self):
        instruction = self.home / ".claude/CLAUDE.md"
        instruction.parent.mkdir(parents=True)
        instruction.write_bytes(b"original\r\n")
        self.run_cli("install.py", "--runtime", "claude", "--dry-run")
        self.assertEqual(instruction.read_bytes(), b"original\r\n")
        self.assertFalse((self.home / ".zerobear-os").exists())

    def test_recorded_legacy_link_is_reused_without_changing_source(self):
        source = ROOT / "skills/simple-word"
        original = (source / "SKILL.md").read_bytes()
        target = self.home / ".claude/skills/simple-word"
        create_link(target, source)
        save_state(self.home, {"version": 1, "source_root": str(ROOT), "items": {
            ".claude/skills/simple-word": {"kind": "link", "target": str(source)}
        }})
        self.run_cli("install.py", "--runtime", "claude")
        self.assertTrue(linked(target))
        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        self.assertEqual((source / "SKILL.md").read_bytes(), original)
        self.run_cli("uninstall.py")
        self.assertEqual((source / "SKILL.md").read_bytes(), original)

    def test_owned_copy_is_migrated_to_link_without_changing_source(self):
        target = self.home / ".claude/skills/simple-word"
        from scripts.install_support import copy_skill, directory_snapshot
        source = ROOT / "skills/simple-word"
        copy_skill(source, target, directory_snapshot(source))
        (target / "stale.txt").write_bytes(b"old resource")
        state = {"version": 1, "items": {".claude/skills/simple-word": {
            "kind": "directory", "snapshot": directory_snapshot(target)
        }}}
        save_state(self.home, state)
        self.run_cli("install.py", "--runtime", "claude")
        self.assertFalse((target / "stale.txt").exists())
        self.assertTrue(linked(target))
        self.run_cli("uninstall.py")

    def test_link_reads_updated_source_without_reinstallation(self):
        source = self.home / "source"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_bytes(b"version one")
        target = self.home / ".claude/skills/example"
        create_link(target, source)
        self.assertEqual((target / "SKILL.md").read_bytes(), b"version one")
        (source / "SKILL.md").write_bytes(b"version two")
        (source / "new-reference.md").write_bytes(b"new reference")
        self.assertEqual((target / "SKILL.md").read_bytes(), b"version two")
        self.assertEqual((target / "new-reference.md").read_bytes(), b"new reference")
        remove_link(target)
        self.assertEqual((source / "SKILL.md").read_bytes(), b"version two")


if __name__ == "__main__":
    unittest.main()
