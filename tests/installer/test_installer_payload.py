"""Payload scanning and the ownership class of every payload path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from installer.errors import InstallError
from installer.payload import Ownership, classify, kit_version, scan_payload

pytest_plugins = ("installer_fixtures",)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Kit-owned: replaced on upgrade.
        (".claude/reviewkit/rk.py", Ownership.KIT),
        (".claude/reviewkit/lib/reviewkit/config.py", Ownership.KIT),
        (".claude/reviewkit/lib/reviewkit/github/client.py", Ownership.KIT),
        (".claude/hooks/commit_gate.py", Ownership.KIT),
        (".claude/skills/pr-review/SKILL.md", Ownership.KIT),
        (".claude/skills/bug-finding/references/patterns.md", Ownership.KIT),
        (".claude/skills/finding-verification/SKILL.md", Ownership.KIT),
        (".claude/agents/bug-finder.md", Ownership.KIT),
        (".claude/agents/finding-verifier.md", Ownership.KIT),
        (".github/workflows/claude-review.yml", Ownership.KIT),
        (".github/ISSUE_TEMPLATE/bug.yml", Ownership.KIT),
        # Create-if-absent: the user's once created.
        ("REVIEW.md", Ownership.USER),
        ("docs/reliability-checklist.md", Ownership.USER),
        (".claude/skills/verify/SKILL.md", Ownership.USER),
        (".claude/skills/verify/scripts/run.sh", Ownership.USER),
        (".github/pull_request_template.md", Ownership.USER),
        (".github/dependabot.yml", Ownership.USER),
        # Special: merged or generated, never copied verbatim.
        (".claude/settings.json", Ownership.SETTINGS),
        (".claude/reviewkit.json", Ownership.GENERATED),
        (".claude/reviewkit/manifest.json", Ownership.GENERATED),
        # Anything else in the payload belongs to the kit.
        ("scripts/extra.sh", Ownership.KIT),
        ("docs/other.md", Ownership.KIT),
        ("services/REVIEW.md", Ownership.KIT),
        (".claude/agents/another.md", Ownership.KIT),
    ],
)
def test_classify(path: str, expected: Ownership) -> None:
    assert classify(path) is expected


@pytest.mark.parametrize("path", ["CLAUDE.md", ".gitignore", ".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"])
def test_files_that_carry_a_managed_block_are_never_copied(path: str) -> None:
    # They are edited in place through their managed block; copying a payload
    # version over them (worse, with --force) would destroy the user's file.
    assert classify(path) is Ownership.GENERATED


def test_scan_lists_files_sorted_and_classified(kit) -> None:
    files = scan_payload(kit.payload)
    paths = [item.path for item in files]
    assert paths == sorted(paths)
    by_path = {item.path: item for item in files}
    assert by_path[".claude/hooks/gate.py"].ownership is Ownership.KIT
    assert by_path["REVIEW.md"].ownership is Ownership.USER
    assert by_path[".claude/settings.json"].ownership is Ownership.SETTINGS
    assert by_path[".claude/reviewkit.json"].ownership is Ownership.GENERATED
    assert by_path[".claude/hooks/gate.py"].source == kit.payload / ".claude" / "hooks" / "gate.py"


def test_scan_skips_bytecode(kit) -> None:
    kit.write("tools/__pycache__/deep/module.py", "x = 1\n")
    kit.write(".DS_Store", "junk")
    paths = [item.path for item in scan_payload(kit.payload)]
    assert not [path for path in paths if "__pycache__" in path or path.endswith(".pyc")]
    assert ".DS_Store" not in paths


def test_scan_reports_the_executable_bit(kit) -> None:
    by_path = {item.path: item for item in scan_payload(kit.payload)}
    assert by_path[".claude/reviewkit/rk.py"].executable is True
    assert by_path[".claude/hooks/gate.py"].executable is False


def test_scan_rejects_a_missing_payload(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="payload"):
        scan_payload(tmp_path / "nowhere")


def test_scan_rejects_symlinks_in_the_payload(kit, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    os.symlink(outside, kit.payload / "linked.txt")
    with pytest.raises(InstallError, match="symbolic link: linked.txt"):
        scan_payload(kit.payload)


def test_scan_rejects_symlinked_directories_in_the_payload(kit, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, kit.payload / "linked-dir")
    with pytest.raises(InstallError, match="symbolic link: linked-dir"):
        scan_payload(kit.payload)


def test_scan_rejects_payload_files_inside_git_metadata(kit) -> None:
    kit.write(".git/hooks/pre-commit", "#!/bin/sh\n")
    with pytest.raises(InstallError, match=r"\.git"):
        scan_payload(kit.payload)


def test_kit_version_comes_from_the_library(kit) -> None:
    assert kit_version(kit.payload) == "9.9.9"


def test_kit_version_without_the_library(tmp_path: Path) -> None:
    (tmp_path / "REVIEW.md").write_text("x\n", encoding="utf-8")
    assert kit_version(tmp_path) == "0"


def test_kit_version_when_the_library_declares_none(kit) -> None:
    kit.write(".claude/reviewkit/lib/reviewkit/__init__.py", '"""No version here."""\n')
    assert kit_version(kit.payload) == "0"
