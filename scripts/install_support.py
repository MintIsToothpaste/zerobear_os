"""Shared storage and filesystem operations for the personal OS installer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path


STATE_DIR = ".zerobear-os"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
SKILL_ROOTS = (".agents/skills", ".codex/skills", ".claude/skills")
INSTRUCTION_PATHS = (".codex/AGENTS.md", ".claude/CLAUDE.md")


def present(path: Path) -> bool:
    return os.path.lexists(path)


def linked(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(home: Path, relative: str) -> Path:
    """Only allow known installation paths and never traverse linked parents."""
    parts = relative.split("/")
    valid = relative in INSTRUCTION_PATHS or (
        len(parts) == 3
        and "/".join(parts[:2]) in SKILL_ROOTS
        and NAME.fullmatch(parts[2]) is not None
    )
    if not valid:
        raise ValueError(f"Invalid installation path: {relative}")
    path = home.joinpath(*parts)
    for parent in path.parents:
        if parent == home:
            break
        if linked(parent):
            raise ValueError(f"Linked installation parent is not supported: {parent}")
    return path


def state_directory(home: Path) -> Path:
    path = home / STATE_DIR
    if linked(path):
        raise ValueError(f"Linked state directory is not supported: {path}")
    return path


def load_state(home: Path) -> dict:
    path = state_directory(home) / "install.json"
    if linked(path):
        raise ValueError(f"Linked manifest is not supported: {path}")
    if not path.exists():
        return {"version": 1, "items": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("items"), dict):
        raise ValueError(f"Unsupported installation manifest: {path}")
    for relative, item in data["items"].items():
        safe_path(home, relative)
        if not isinstance(item, dict) or item.get("kind") not in ("link", "file", "directory"):
            raise ValueError(f"Invalid manifest item: {relative}")
        if item["kind"] == "link":
            if "/".join(relative.split("/")[:2]) not in SKILL_ROOTS:
                raise ValueError(f"Invalid skill link: {relative}")
            if not isinstance(item.get("target"), str) or not Path(item["target"]).is_absolute():
                raise ValueError(f"Invalid link target: {relative}")
        elif item["kind"] == "directory":
            if "/".join(relative.split("/")[:2]) not in SKILL_ROOTS or not isinstance(item.get("snapshot"), dict):
                raise ValueError(f"Invalid skill copy: {relative}")
        elif relative not in INSTRUCTION_PATHS or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
            raise ValueError(f"Invalid instruction record: {relative}")
    return data


def save_state(home: Path, state: dict) -> None:
    directory = state_directory(home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "install.json"
    if linked(path):
        raise ValueError(f"Linked manifest is not supported: {path}")
    temporary = directory / "install.json.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(state, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def installation_lock(home: Path, dry_run: bool):
    if dry_run:
        yield
        return
    directory = state_directory(home)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "install.lock"
    try:
        with lock.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise ValueError(f"Installer lock exists; check for a running installer: {lock}") from exc
    try:
        yield
    finally:
        lock.unlink()


def remove_link(path: Path) -> None:
    # Never recurse: rmdir removes a Windows junction itself, not its contents.
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()


def create_link(path: Path, target: Path) -> None:
    """Prefer a relative symlink; use a junction when Windows denies symlinks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        relative = os.path.relpath(target, start=path.parent)
    except ValueError:
        # Windows cannot express a relative path across different drive letters.
        relative = str(target)
    try:
        path.symlink_to(relative, target_is_directory=True)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) not in (5, 1314):
            raise
        environment = os.environ.copy()
        environment["ZEROBEAR_LINK_PATH"] = str(path)
        environment["ZEROBEAR_LINK_TARGET"] = str(target)
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction "
            "-Path $env:ZEROBEAR_LINK_PATH -Target $env:ZEROBEAR_LINK_TARGET | Out-Null",
        ], env=environment, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"Junction creation failed for {path}: {result.stderr.decode('utf-8', errors='replace')}")
    if not linked(path) or path.resolve() != target.resolve():
        raise ValueError(f"Link verification failed: {path}")


def owned(path: Path, item: dict) -> bool:
    if item["kind"] == "link":
        return linked(path) and path.resolve() == Path(item["target"]).resolve()
    if item["kind"] == "directory":
        try:
            return not linked(path) and path.is_dir() and directory_snapshot(path) == item["snapshot"]
        except (OSError, ValueError):
            return False
    return not linked(path) and path.is_file() and digest(path) == item["sha256"]


def directory_snapshot(path: Path) -> dict:
    """Hash complete skill contents, including empty directories; reject links."""
    if linked(path) or not path.is_dir():
        raise ValueError(f"Expected a real skill directory: {path}")
    files = {}
    directories = []
    for current, folders, names in os.walk(path, followlinks=False):
        current_path = Path(current)
        for name in folders + names:
            if linked(current_path / name):
                raise ValueError(f"Linked skill contents are not supported: {current_path / name}")
        for name in folders:
            directories.append((current_path / name).relative_to(path).as_posix())
        for name in names:
            file = current_path / name
            if not file.is_file():
                raise ValueError(f"Unsupported skill entry: {file}")
            files[file.relative_to(path).as_posix()] = digest(file)
    return {"files": files, "directories": sorted(directories)}


def copy_skill(source: Path, destination: Path, snapshot: dict) -> None:
    if directory_snapshot(source) != snapshot:
        raise ValueError(f"Source changed during installation: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    if directory_snapshot(destination) != snapshot:
        raise ValueError(f"Skill copy verification failed: {destination}")


def remove_item(home: Path, relative: str, item: dict) -> None:
    path = safe_path(home, relative)
    if item["kind"] == "link":
        remove_link(path)
    elif item["kind"] == "directory":
        # Check the absolute deletion boundary and all entries before recursion.
        if linked(path) or path.resolve() != path.absolute() or not path.resolve().is_relative_to(home.resolve()):
            raise ValueError(f"Unsafe skill removal target: {path}")
        directory_snapshot(path)
        shutil.rmtree(path)
    else:
        path.unlink()
