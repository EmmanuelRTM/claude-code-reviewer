"""Safety: symlinks are never written through, dry runs write nothing, only git is ever run."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from installer_kit import KIT_FILES, USER_FILES, snapshot

from installer.errors import InstallError
from installer.operations import install, uninstall
from installer.report import CREATED, ERROR, KEPT, MERGED, UNCHANGED

pytest_plugins = ("installer_fixtures",)

GATE = ".claude/hooks/gate.py"
STOP = ".claude/hooks/stop.py"
MANIFEST = ".claude/reviewkit/manifest.json"
SETTINGS = ".claude/settings.json"


def detail_of(report, path: str) -> str:
    return next(entry.detail for entry in report.entries if entry.path == path)


def link(repo, relative: str, destination: Path) -> Path:
    path = repo.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(destination, path)
    return path


# --- symbolic links ----------------------------------------------------------------


def test_a_symlinked_destination_is_refused_even_with_force(kit, repo, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("precious\n", encoding="utf-8")
    symlink = link(repo, GATE, outside)

    report = kit.install(repo, force=True)

    assert not report.ok
    assert report.status_of(GATE) == ERROR
    assert "symbolic link" in detail_of(report, GATE)
    assert outside.read_text(encoding="utf-8") == "precious\n"
    assert os.path.islink(symlink)
    # Only that path was refused: its neighbour was installed, and it is not recorded as installed.
    assert (repo.path / STOP).read_text(encoding="utf-8") == KIT_FILES[STOP]
    manifest = json.loads((repo.path / MANIFEST).read_text(encoding="utf-8"))
    assert GATE not in manifest["files"] and STOP in manifest["files"]


def test_a_symlinked_parent_directory_is_refused(kit, repo, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link(repo, ".claude/hooks", outside)

    report = kit.install(repo, force=True)

    for path in (GATE, STOP):
        assert report.status_of(path) == ERROR
        assert ".claude/hooks" in detail_of(report, path) and "symbolic link" in detail_of(report, path)
    assert os.listdir(outside) == []
    assert (repo.path / ".claude/reviewkit/rk.py").exists()


def test_hooks_are_not_wired_while_the_scripts_they_run_are_missing(kit, repo, tmp_path: Path) -> None:
    # `python3 missing.py` exits with status 2, which Claude Code treats as a blocking hook error.
    # Wiring hooks to scripts that could not be installed would block every session.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link(repo, ".claude/hooks", outside)

    report = kit.install(repo)

    assert report.status_of(SETTINGS) == ERROR
    assert GATE in detail_of(report, SETTINGS)
    assert not (repo.path / SETTINGS).exists()

    os.unlink(repo.path / ".claude/hooks")  # the obstacle is gone: the same command now completes
    report = kit.install(repo)
    assert report.ok
    assert report.status_of(SETTINGS) == MERGED
    assert (repo.path / GATE).exists()


def test_hooks_that_are_already_wired_stay_when_a_script_cannot_be_updated(kit, repo, tmp_path: Path) -> None:
    kit.install(repo)
    before = (repo.path / SETTINGS).read_bytes()
    outside = tmp_path / "outside.py"
    outside.write_text("print('theirs')\n", encoding="utf-8")
    (repo.path / GATE).unlink()
    os.symlink(outside, repo.path / GATE)

    report = kit.install(repo)

    assert report.status_of(GATE) == ERROR
    assert report.status_of(SETTINGS) == UNCHANGED
    assert (repo.path / SETTINGS).read_bytes() == before


@pytest.mark.parametrize("path", ["CLAUDE.md", ".gitignore", ".github/CODEOWNERS", ".claude/settings.json", MANIFEST])
def test_merged_and_generated_files_are_not_written_through_a_symlink(kit, repo, tmp_path: Path, path: str) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("{}\n", encoding="utf-8")
    if (repo.path / path).exists():
        (repo.path / path).unlink()
    link(repo, path, outside)

    report = kit.install(repo, force=True)

    assert report.status_of(path) == ERROR
    assert "symbolic link" in detail_of(report, path)
    assert outside.read_text(encoding="utf-8") == "{}\n"
    assert os.path.islink(repo.path / path)


@pytest.mark.parametrize("path", [".claude/reviewkit.json", ".claude"])
def test_a_symlink_on_the_way_to_the_configuration_stops_the_run(kit, repo, tmp_path: Path, path: str) -> None:
    outside = tmp_path / "outside"
    if path == ".claude":
        outside.mkdir()
    else:
        outside.write_text("{}\n", encoding="utf-8")
    link(repo, path, outside)
    before = snapshot(repo.path)

    with pytest.raises(InstallError, match="symbolic link"):
        kit.install(repo, force=True)

    assert snapshot(repo.path) == before
    if path == ".claude":
        assert os.listdir(outside) == []
    else:
        assert outside.read_text(encoding="utf-8") == "{}\n"


def test_a_user_owned_file_reached_through_a_symlink_is_simply_kept(kit, repo) -> None:
    repo.write("docs/REVIEW-real.md", "# my rules\n")
    link(repo, "REVIEW.md", Path("docs/REVIEW-real.md"))
    report = kit.install(repo)
    assert report.status_of("REVIEW.md") == KEPT  # nothing would be written there anyway
    assert (repo.path / "docs/REVIEW-real.md").read_text(encoding="utf-8") == "# my rules\n"


def test_a_dangling_symlink_at_a_user_owned_path_is_refused(kit, repo, tmp_path: Path) -> None:
    outside = tmp_path / "does-not-exist.md"
    link(repo, "REVIEW.md", outside)
    report = kit.install(repo)
    assert report.status_of("REVIEW.md") == ERROR
    assert not outside.exists()


def test_a_user_owned_file_is_not_created_through_a_symlinked_directory(kit, repo, tmp_path: Path) -> None:
    outside = tmp_path / "shared-docs"
    outside.mkdir()
    link(repo, "docs", outside)
    report = kit.install(repo)
    assert report.status_of("docs/reliability-checklist.md") == ERROR
    assert os.listdir(outside) == []

    (outside / "reliability-checklist.md").write_text("# theirs\n", encoding="utf-8")
    assert kit.install(repo).status_of("docs/reliability-checklist.md") == KEPT
    assert (outside / "reliability-checklist.md").read_text(encoding="utf-8") == "# theirs\n"


def test_a_directory_where_a_file_belongs_is_reported(kit, repo) -> None:
    (repo.path / "CLAUDE.md").mkdir()
    report = kit.install(repo)
    assert report.status_of("CLAUDE.md") == ERROR
    assert "not a regular file" in detail_of(report, "CLAUDE.md")
    assert (repo.path / "CLAUDE.md").is_dir()


def test_a_target_reached_through_a_symlink_is_resolved_first(kit, repo, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    os.symlink(repo.path, alias)
    report = install(alias, payload_root=kit.payload, blocks_root=kit.blocks)
    assert report.ok
    assert report.target == repo.path.resolve()
    assert (repo.path / GATE).exists()


# --- dry run -------------------------------------------------------------------------


def test_a_dry_run_writes_nothing_at_all(kit, repo) -> None:
    before = snapshot(repo.path)  # every file and directory, .git included
    report = kit.install(repo, dry_run=True)
    assert report.dry_run and report.ok
    assert report.status_of(GATE) == CREATED  # the plan says what would happen
    assert snapshot(repo.path) == before
    assert not (repo.path / ".claude").exists()  # not even a directory


def test_a_dry_run_predicts_the_real_run(kit, repo) -> None:
    repo.write("CLAUDE.md", "# Project\n")
    repo.write(".github/workflows/claude-review.yml", "name: mine\n")
    planned = kit.install(repo, dry_run=True)
    actual = kit.install(repo)
    assert [(entry.path, entry.status) for entry in planned.entries] == [
        (entry.path, entry.status) for entry in actual.entries
    ]


def test_dry_runs_of_an_upgrade_and_an_uninstall_write_nothing(kit, repo) -> None:
    kit.install(repo)
    kit.write(GATE, "print('v2')\n")
    kit.remove(STOP)
    before = snapshot(repo.path)
    upgrade = kit.install(repo, dry_run=True, force=True, overrides={"gate": {"mode": "enforce"}})
    assert upgrade.changed
    assert snapshot(repo.path) == before
    removal = kit.uninstall(repo, dry_run=True, force=True)
    assert removal.changed and removal.dry_run
    assert snapshot(repo.path) == before


# --- the target must be a git work tree ---------------------------------------------------


def test_a_directory_that_is_not_a_repository_is_refused(kit, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    for operation in (install, uninstall):
        with pytest.raises(InstallError, match="git work tree"):
            operation(plain, payload_root=kit.payload)
    assert os.listdir(plain) == []


def test_a_subdirectory_of_a_repository_is_refused(kit, repo) -> None:
    package = repo.path / "packages" / "app"
    package.mkdir(parents=True)
    with pytest.raises(InstallError, match="top level"):
        install(package, payload_root=kit.payload, blocks_root=kit.blocks)
    assert os.listdir(package) == []


def test_a_missing_target_is_refused(kit, tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="not an existing directory"):
        install(tmp_path / "nowhere", payload_root=kit.payload, blocks_root=kit.blocks)
    target_file = tmp_path / "file.txt"
    target_file.write_text("x", encoding="utf-8")
    with pytest.raises(InstallError, match="not an existing directory"):
        install(target_file, payload_root=kit.payload, blocks_root=kit.blocks)


# --- nothing from the target is executed ------------------------------------------------------


def test_the_only_subprocess_is_a_read_only_git_query(kit, repo, monkeypatch: pytest.MonkeyPatch) -> None:
    # A repository full of things that would run if anything executed its content.
    repo.write("package.json", json.dumps({"scripts": {"test": "touch PWNED", "preinstall": "touch PWNED"}}))
    repo.write("Makefile", "test:\n\ttouch PWNED\n")
    repo.write("conftest.py", "open('PWNED', 'w').close()\n")
    repo.write("setup.py", "open('PWNED', 'w').close()\n")
    repo.write("sitecustomize.py", "open('PWNED', 'w').close()\n")
    repo.write("reviewkit/__init__.py", "open('PWNED', 'w').close()\n")
    hook = repo.write(".git/hooks/post-checkout", "#!/bin/sh\ntouch PWNED\n")
    hook.chmod(0o755)

    commands: list = []
    real_popen = subprocess.Popen

    class RecordingPopen(real_popen):  # type: ignore[type-arg]
        def __init__(self, args, *positional, **keywords) -> None:
            commands.append(list(args))
            super().__init__(args, *positional, **keywords)

    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    monkeypatch.chdir(repo.path)  # even when run from inside the target

    assert kit.install(repo).ok
    first_run = [command[3] for command in commands]
    assert kit.install(repo, force=True).ok
    assert kit.uninstall(repo).ok

    for command in commands:
        assert command[:3] == ["git", "-C", str(repo.path.resolve())]
        assert command[3] in ("rev-parse", "symbolic-ref"), command
    assert not (repo.path / "PWNED").exists()
    # One query to check the target, one to detect the default branch for a new configuration.
    assert first_run == ["rev-parse", "symbolic-ref"]
    # Later runs find a configuration and detect nothing.
    assert [command[3] for command in commands[2:]] == ["rev-parse", "rev-parse"]


def test_every_installed_path_stays_inside_the_target(kit, repo, tmp_path: Path) -> None:
    siblings_before = sorted(os.listdir(tmp_path))
    report = kit.install(repo)
    assert sorted(os.listdir(tmp_path)) == siblings_before
    for entry in report.entries:
        assert not entry.path.startswith(("/", "..")) and "\\" not in entry.path
    assert set(USER_FILES) <= {entry.path for entry in report.entries}
