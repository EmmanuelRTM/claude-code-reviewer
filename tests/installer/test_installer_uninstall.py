"""Uninstall: remove what the installer added and nothing else."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from installer_kit import KIT_FILES, USER_FILES, files_only, snapshot

from installer.report import ERROR, KEPT, REMOVED, SKIPPED, UNCHANGED, UPDATED

pytest_plugins = ("installer_fixtures",)

GATE = ".claude/hooks/gate.py"
CONFIG = ".claude/reviewkit.json"
SETTINGS = ".claude/settings.json"
MANIFEST = ".claude/reviewkit/manifest.json"
KIT_DIRECTORIES = [
    ".claude/reviewkit",
    ".claude/hooks",
    ".claude/skills/pr-review",
    ".claude/skills/bug-finding",
    ".claude/skills/finding-verification",
]
USER_SETTINGS = {
    "permissions": {"allow": ["Bash(npm test)"]},
    "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "./fmt.sh"}]}]},
}


def read(repo, relative: str) -> str:
    return (repo.path / relative).read_text(encoding="utf-8")


def test_round_trip_leaves_user_content_and_user_owned_files(kit, repo) -> None:
    repo.write("CLAUDE.md", "# Project\n\nHow we work.\n")
    repo.write(SETTINGS, json.dumps(USER_SETTINGS, indent=2) + "\n")
    repo.write(".github/CODEOWNERS", "*.js @frontend\n")
    repo.write(".github/workflows/ci.yml", "name: ci\n")
    repo.write("src/app.py", "print('app')\n")
    original = files_only(repo.path)

    assert kit.install(repo).ok
    report = kit.uninstall(repo)

    assert report.ok and report.mode == "uninstall"
    after = files_only(repo.path)
    for path, content in original.items():
        assert after[path] == content, path  # pre-existing content is back, byte for byte
    # What remains beyond the original content is exactly the user-owned files.
    assert set(after) - set(original) == set(USER_FILES) | {CONFIG}
    for path in [*USER_FILES, CONFIG]:
        assert report.status_of(path) == KEPT, path
    for path in KIT_FILES:
        assert report.status_of(path) == REMOVED, path
    assert report.status_of(MANIFEST) == REMOVED
    assert report.status_of("CLAUDE.md") == UPDATED
    assert report.status_of(SETTINGS) == UPDATED
    for directory in KIT_DIRECTORIES:
        assert not (repo.path / directory).exists(), directory
    assert (repo.path / ".claude/skills/verify/SKILL.md").exists()


def test_files_the_installer_created_for_its_blocks_are_emptied_not_deleted(kit, repo) -> None:
    kit.install(repo)
    report = kit.uninstall(repo)
    assert read(repo, "CLAUDE.md") == ""  # never deleted: the user may have plans for it
    assert read(repo, ".github/CODEOWNERS") == ""
    assert read(repo, ".gitignore") == ".claude/state/\n"
    # The settings file held nothing but the kit's hooks.
    assert not (repo.path / SETTINGS).exists()
    assert report.status_of(SETTINGS) == REMOVED


def test_user_hooks_survive(kit, repo) -> None:
    repo.write(SETTINGS, json.dumps(USER_SETTINGS))
    kit.install(repo)
    kit.uninstall(repo)
    assert json.loads(read(repo, SETTINGS)) == USER_SETTINGS


def test_locally_modified_kit_files_are_kept_until_forced(kit, repo) -> None:
    kit.install(repo)
    (repo.path / GATE).write_text("print('my edit')\n", encoding="utf-8")

    report = kit.uninstall(repo)
    assert report.status_of(GATE) == SKIPPED
    assert read(repo, GATE) == "print('my edit')\n"
    assert not (repo.path / ".claude/hooks/stop.py").exists()
    # The manifest still tracks the file that was left, so --force can finish the job later.
    assert report.status_of(MANIFEST) == UPDATED
    assert list(json.loads(read(repo, MANIFEST))["files"]) == [GATE]

    forced = kit.uninstall(repo, force=True)
    assert forced.status_of(GATE) == REMOVED
    assert forced.status_of(MANIFEST) == REMOVED
    for directory in KIT_DIRECTORIES:
        assert not (repo.path / directory).exists(), directory


def test_bytecode_caches_do_not_keep_kit_directories_alive(kit, repo) -> None:
    kit.install(repo)
    cache = repo.path / ".claude/reviewkit/lib/reviewkit/__pycache__"
    cache.mkdir()
    (cache / "config.cpython-313.pyc").write_bytes(b"\0bytecode")
    kit.uninstall(repo)
    assert not (repo.path / ".claude/reviewkit").exists()


def test_files_the_user_added_inside_kit_directories_survive(kit, repo) -> None:
    kit.install(repo)
    repo.write(".claude/hooks/my_hook.py", "print('mine')\n")
    repo.write(".claude/skills/pr-review/notes.md", "my notes\n")
    kit.uninstall(repo)
    assert read(repo, ".claude/hooks/my_hook.py") == "print('mine')\n"
    assert read(repo, ".claude/skills/pr-review/notes.md") == "my notes\n"
    assert not (repo.path / GATE).exists()


def test_uninstall_without_an_install_changes_nothing(kit, repo) -> None:
    repo.write("CLAUDE.md", "# Project\n")
    repo.write("REVIEW.md", "# my rules\n")
    before = snapshot(repo.path)
    report = kit.uninstall(repo)
    assert report.ok and not report.changed
    assert snapshot(repo.path) == before
    assert report.status_of("CLAUDE.md") == UNCHANGED
    assert report.status_of("REVIEW.md") == KEPT
    assert any("manifest" in warning for warning in report.warnings)


def test_a_second_uninstall_is_a_no_op(kit, repo) -> None:
    kit.install(repo)
    kit.uninstall(repo)
    before = snapshot(repo.path)
    report = kit.uninstall(repo)
    assert report.ok and not report.changed
    assert snapshot(repo.path) == before


def test_reinstall_after_uninstall(kit, repo) -> None:
    kit.install(repo, overrides={"commands": {"test": "make check"}})
    kit.uninstall(repo)
    report = kit.install(repo)
    assert report.ok
    assert read(repo, GATE) == KIT_FILES[GATE]
    assert report.status_of("REVIEW.md") == KEPT
    assert report.status_of(CONFIG) == UNCHANGED  # the user's configuration survived both
    assert "- Test: `make check`" in read(repo, "CLAUDE.md")


def test_damaged_markers_stop_only_that_file(kit, repo) -> None:
    kit.install(repo)
    damaged = read(repo, "CLAUDE.md").replace("<!-- reviewkit:end -->\n", "")
    repo.write("CLAUDE.md", damaged)
    report = kit.uninstall(repo)
    assert not report.ok
    assert report.status_of("CLAUDE.md") == ERROR
    assert read(repo, "CLAUDE.md") == damaged
    assert not (repo.path / GATE).exists()
    assert read(repo, ".gitignore") == ".claude/state/\n"


def test_a_corrupt_settings_file_is_left_untouched(kit, repo) -> None:
    kit.install(repo)
    repo.write(SETTINGS, "{broken")
    report = kit.uninstall(repo)
    assert report.status_of(SETTINGS) == ERROR
    assert read(repo, SETTINGS) == "{broken"
    assert not (repo.path / GATE).exists()
    # The scripts are gone, so the user is told to check what the file still wires.
    detail = next(entry.detail for entry in report.entries if entry.path == SETTINGS)
    assert "left untouched" in detail and "by hand" in detail


@pytest.mark.parametrize("location", ["CODEOWNERS", "docs/CODEOWNERS"])
def test_the_block_is_removed_from_wherever_codeowners_lives(kit, repo, location: str) -> None:
    repo.write(location, "* @everyone\n")
    kit.install(repo, overrides={"owners": ["@org/leads"]})
    report = kit.uninstall(repo)
    assert report.status_of(location) == UPDATED
    assert read(repo, location) == "* @everyone\n"


def test_a_tampered_manifest_cannot_remove_user_owned_or_outside_files(kit, repo, tmp_path) -> None:
    kit.install(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("precious\n", encoding="utf-8")
    manifest = json.loads(read(repo, MANIFEST))
    for path in ("REVIEW.md", CONFIG, "CLAUDE.md", SETTINGS):
        manifest["files"][path] = hashlib.sha256((repo.path / path).read_bytes()).hexdigest()
    manifest["files"]["../outside.txt"] = hashlib.sha256(b"precious\n").hexdigest()
    manifest["files"][".git/HEAD"] = hashlib.sha256((repo.path / ".git/HEAD").read_bytes()).hexdigest()
    repo.write(MANIFEST, json.dumps(manifest))

    report = kit.uninstall(repo, force=True)

    assert outside.exists() and (repo.path / ".git/HEAD").exists()
    assert (repo.path / "REVIEW.md").exists() and (repo.path / CONFIG).exists() and (repo.path / "CLAUDE.md").exists()
    assert report.status_of("REVIEW.md") == KEPT
    assert not (repo.path / GATE).exists()


def test_a_symlinked_kit_file_is_not_removed(kit, repo, tmp_path) -> None:
    kit.install(repo)
    outside = tmp_path / "outside.py"
    outside.write_text(KIT_FILES[GATE], encoding="utf-8")  # same content: only the link betrays it
    (repo.path / GATE).unlink()
    os.symlink(outside, repo.path / GATE)
    report = kit.uninstall(repo, force=True)
    assert report.status_of(GATE) == ERROR
    assert outside.exists() and os.path.islink(repo.path / GATE)
