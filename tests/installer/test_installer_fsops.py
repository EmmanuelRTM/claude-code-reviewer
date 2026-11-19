"""Filesystem primitives: path validation, symlink refusal, atomic writes, pruning."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from installer import fsops
from installer.fsops import (
    UnsafePathError,
    delete_file,
    inspect,
    prune_empty_dirs,
    relative_parts,
    write_file,
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_relative_parts_splits_a_clean_path() -> None:
    assert relative_parts(".claude/hooks/gate.py") == (".claude", "hooks", "gate.py")
    assert relative_parts("REVIEW.md") == ("REVIEW.md",)
    assert relative_parts(".github/workflows/x.yml") == (".github", "workflows", "x.yml")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/etc/passwd",
        "../outside",
        "a/../b",
        "a//b",
        "./a",
        "a/./b",
        "a/",
        "a\\b",
        "a\0b",
        ".git/config",
        "sub/.git/hooks/pre-commit",
        ".GIT/config",
    ],
)
def test_relative_parts_rejects_unsafe_paths(bad: str) -> None:
    with pytest.raises(UnsafePathError):
        relative_parts(bad)


def test_inspect_absent_path(tmp_path: Path) -> None:
    found = inspect(tmp_path, "docs/missing.md")
    assert found.problem is None
    assert found.content is None
    assert found.exists is False


def test_inspect_regular_file(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_bytes(b"hello\n")
    found = inspect(tmp_path, "notes.md")
    assert (found.problem, found.content, found.exists) == (None, b"hello\n", True)
    (tmp_path / "empty.md").write_bytes(b"")
    assert inspect(tmp_path, "empty.md").exists is True  # empty is not absent


def test_inspect_refuses_a_symlinked_destination(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text("real\n", encoding="utf-8")
    os.symlink("real.md", tmp_path / "CLAUDE.md")
    found = inspect(tmp_path, "CLAUDE.md")
    assert "symbolic link" in (found.problem or "")
    assert found.content is None


def test_inspect_refuses_a_dangling_symlink(tmp_path: Path) -> None:
    os.symlink("nowhere", tmp_path / "CLAUDE.md")
    assert "symbolic link" in (inspect(tmp_path, "CLAUDE.md").problem or "")


def test_inspect_refuses_a_symlinked_parent_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    (root / ".claude").mkdir(parents=True)
    os.symlink(outside, root / ".claude" / "hooks")
    problem = inspect(root, ".claude/hooks/gate.py").problem or ""
    assert "symbolic link" in problem
    assert ".claude/hooks" in problem


def test_inspect_reports_a_directory_where_a_file_belongs(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").mkdir()
    assert "not a regular file" in (inspect(tmp_path, "CLAUDE.md").problem or "")


def test_inspect_reports_a_file_where_a_directory_belongs(tmp_path: Path) -> None:
    (tmp_path / "docs").write_text("oops\n", encoding="utf-8")
    assert "not a directory" in (inspect(tmp_path, "docs/reliability-checklist.md").problem or "")


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    write_file(tmp_path, "a/b/c.txt", b"content\n")
    assert (tmp_path / "a" / "b" / "c.txt").read_bytes() == b"content\n"
    assert os.listdir(tmp_path / "a" / "b") == ["c.txt"]  # no temporary file left behind


def test_write_sets_and_clears_the_executable_bit(tmp_path: Path) -> None:
    write_file(tmp_path, "run.py", b"x\n", executable=True)
    assert mode_of(tmp_path / "run.py") & 0o100
    write_file(tmp_path, "run.py", b"y\n", executable=False)
    assert not mode_of(tmp_path / "run.py") & 0o111


def test_write_keeps_the_existing_mode_when_not_told_otherwise(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"{}\n")
    target.chmod(0o600)
    write_file(tmp_path, "settings.json", b'{"a": 1}\n')
    assert mode_of(target) == 0o600
    assert target.read_bytes() == b'{"a": 1}\n'


def test_new_files_follow_the_umask(tmp_path: Path) -> None:
    previous = os.umask(0o027)
    try:
        write_file(tmp_path, "plain.txt", b"x\n", executable=False)
        write_file(tmp_path, "tool.sh", b"x\n", executable=True)
    finally:
        os.umask(previous)
    assert mode_of(tmp_path / "plain.txt") == 0o640
    assert mode_of(tmp_path / "tool.sh") == 0o750


def test_write_refuses_a_symlinked_destination(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"precious\n")
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(victim, root / "CLAUDE.md")
    with pytest.raises(UnsafePathError, match="symbolic link"):
        write_file(root, "CLAUDE.md", b"overwritten\n")
    assert victim.read_bytes() == b"precious\n"
    assert os.path.islink(root / "CLAUDE.md")


def test_write_refuses_a_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside, root / "docs")
    with pytest.raises(UnsafePathError, match="symbolic link"):
        write_file(root, "docs/reliability-checklist.md", b"x\n")
    assert os.listdir(outside) == []


def test_write_refuses_paths_that_leave_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        write_file(root, "../escaped.txt", b"x\n")
    assert not (tmp_path / "escaped.txt").exists()


def test_a_failed_write_leaves_the_original_and_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"original\n")

    def boom(source: str, destination: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fsops.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_file(tmp_path, "file.txt", b"new\n")
    assert target.read_bytes() == b"original\n"
    assert os.listdir(tmp_path) == ["file.txt"]


def test_delete_file(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.txt").write_bytes(b"x")
    delete_file(tmp_path, "a/x.txt")
    assert not (tmp_path / "a" / "x.txt").exists()
    delete_file(tmp_path, "a/x.txt")  # already gone: not an error


def test_delete_refuses_symlinks(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"precious\n")
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(victim, root / "link.txt")
    with pytest.raises(UnsafePathError):
        delete_file(root, "link.txt")
    assert victim.exists() and os.path.islink(root / "link.txt")


def test_prune_removes_empty_directories_up_to_the_allowed_root(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "hooks" / "deep" / "deeper").mkdir(parents=True)
    prune_empty_dirs(tmp_path, ".claude/hooks/deep/deeper", (".claude/hooks",))
    assert not (tmp_path / ".claude" / "hooks").exists()
    assert (tmp_path / ".claude").is_dir()  # outside the allowed root: kept even though empty


def test_prune_stops_at_the_first_directory_with_content(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "hooks" / "deep").mkdir(parents=True)
    (tmp_path / ".claude" / "hooks" / "mine.py").write_bytes(b"x")
    prune_empty_dirs(tmp_path, ".claude/hooks/deep", (".claude/hooks",))
    assert not (tmp_path / ".claude" / "hooks" / "deep").exists()
    assert (tmp_path / ".claude" / "hooks" / "mine.py").exists()


def test_prune_ignores_directories_outside_the_allowed_roots(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    prune_empty_dirs(tmp_path, ".github/workflows", (".claude/hooks",))
    assert (tmp_path / ".github" / "workflows").is_dir()
    prune_empty_dirs(tmp_path, ".claude/hooks-extra", (".claude/hooks",))  # prefix match is not containment


def test_prune_drops_bytecode_caches_left_by_the_kit(tmp_path: Path) -> None:
    package = tmp_path / ".claude" / "reviewkit" / "lib" / "reviewkit"
    (package / "__pycache__").mkdir(parents=True)
    (package / "__pycache__" / "config.cpython-313.pyc").write_bytes(b"\0")
    prune_empty_dirs(tmp_path, ".claude/reviewkit/lib/reviewkit", (".claude/reviewkit",))
    assert not (tmp_path / ".claude" / "reviewkit").exists()


def test_prune_keeps_a_cache_directory_that_holds_other_files(tmp_path: Path) -> None:
    package = tmp_path / ".claude" / "reviewkit" / "lib"
    (package / "__pycache__").mkdir(parents=True)
    (package / "__pycache__" / "notes.txt").write_bytes(b"mine")
    prune_empty_dirs(tmp_path, ".claude/reviewkit/lib", (".claude/reviewkit",))
    assert (package / "__pycache__" / "notes.txt").exists()


def test_prune_never_follows_a_symlinked_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    (root / ".claude").mkdir(parents=True)
    os.symlink(outside, root / ".claude" / "hooks")
    prune_empty_dirs(root, ".claude/hooks", (".claude/hooks",))
    assert outside.is_dir() and os.path.islink(root / ".claude" / "hooks")
