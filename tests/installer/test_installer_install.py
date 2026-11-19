"""Installing and upgrading: every class of file, the manifest, and a no-op second run."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from installer_kit import FRAGMENT, KIT_FILES, USER_FILES, snapshot

from installer.report import CREATED, KEPT, MERGED, REMOVED, SKIPPED, UNCHANGED, UPDATED
from reviewkit.config import Config, config_to_dict

pytest_plugins = ("installer_fixtures",)

GATE = ".claude/hooks/gate.py"
STOP = ".claude/hooks/stop.py"
MANIFEST = ".claude/reviewkit/manifest.json"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read(repo, relative: str) -> str:
    return (repo.path / relative).read_text(encoding="utf-8")


def manifest_of(repo) -> dict:
    return json.loads(read(repo, MANIFEST))


# --- fresh install ---------------------------------------------------------------


def test_fresh_install_lands_every_class_of_file(kit, repo) -> None:
    report = kit.install(repo)
    assert report.ok
    assert report.mode == "install" and report.dry_run is False and report.kit_version == "9.9.9"

    for path, content in {**KIT_FILES, **USER_FILES}.items():
        assert read(repo, path) == content, path
        assert report.status_of(path) == CREATED, path

    # The configuration is generated from the library defaults, not copied from the payload.
    assert json.loads(read(repo, ".claude/reviewkit.json")) == config_to_dict(Config())
    assert read(repo, ".claude/reviewkit.json").endswith("}\n")
    assert report.status_of(".claude/reviewkit.json") == CREATED

    # The settings fragment became the settings file.
    assert json.loads(read(repo, ".claude/settings.json")) == FRAGMENT
    assert report.status_of(".claude/settings.json") == MERGED

    # Managed blocks: two new files, one appended to the existing .gitignore.
    assert read(repo, "CLAUDE.md").startswith("<!-- reviewkit:begin -->\n")
    assert read(repo, "CLAUDE.md").endswith("<!-- reviewkit:end -->\n")
    assert report.status_of("CLAUDE.md") == CREATED
    assert read(repo, ".github/CODEOWNERS").splitlines()[0] == "# reviewkit:begin"
    assert report.status_of(".github/CODEOWNERS") == CREATED
    assert read(repo, ".gitignore") == (
        ".claude/state/\n\n# reviewkit:begin\n.claude/state/\n.claude/settings.local.json\n# reviewkit:end\n"
    )
    assert report.status_of(".gitignore") == MERGED
    assert report.status_of(MANIFEST) == CREATED


def test_install_into_a_repository_without_any_files(kit, make_repo) -> None:
    empty = make_repo(initial_commit=False)
    (empty.path / ".gitignore").unlink()
    report = kit.install(empty)
    assert report.ok
    assert report.status_of(".gitignore") == CREATED
    assert (
        read(empty, ".gitignore") == "# reviewkit:begin\n.claude/state/\n.claude/settings.local.json\n# reviewkit:end\n"
    )
    assert kit.install(empty).changed is False


def test_manifest_records_exactly_the_kit_owned_files(kit, repo) -> None:
    kit.install(repo)
    manifest = manifest_of(repo)
    assert set(manifest) == {"kit_version", "files"}
    assert manifest["kit_version"] == "9.9.9"
    assert manifest["files"] == {path: sha(content) for path, content in KIT_FILES.items()}
    for path in manifest["files"]:  # the recorded hash is the sha256 of what is on disk
        assert manifest["files"][path] == hashlib.sha256((repo.path / path).read_bytes()).hexdigest()
    text = read(repo, MANIFEST)
    assert text == json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def test_bytecode_and_generated_payload_files_are_not_copied(kit, repo) -> None:
    kit.install(repo)
    installed = [path for path in snapshot(repo.path) if not path.startswith(".git/")]
    assert not [path for path in installed if "__pycache__" in path or path.endswith(".pyc")]
    assert json.loads(read(repo, ".claude/reviewkit.json"))["gate"]["mode"] == "observe"  # payload copy said enforce


def test_the_executable_bit_is_preserved(kit, repo) -> None:
    kit.install(repo)
    assert os.stat(repo.path / ".claude/reviewkit/rk.py").st_mode & stat.S_IXUSR
    assert not os.stat(repo.path / GATE).st_mode & 0o111


def test_no_temporary_files_are_left_behind(kit, repo) -> None:
    kit.install(repo)
    assert not [path for path in snapshot(repo.path) if path.endswith(".tmp")]


def test_a_second_run_is_a_complete_no_op(kit, repo) -> None:
    kit.install(repo)
    before = snapshot(repo.path)
    report = kit.install(repo)
    assert report.ok
    assert report.changed is False
    assert {entry.status for entry in report.entries} == {UNCHANGED, KEPT}
    assert report.paths(KEPT) == sorted(USER_FILES)
    assert snapshot(repo.path) == before  # same bytes, modes and modification times: nothing was rewritten


def test_a_payload_without_a_library_has_version_zero(kit, repo) -> None:
    for path in [path for path in KIT_FILES if path.startswith(".claude/reviewkit/")]:
        kit.remove(path)
    assert kit.install(repo).kit_version == "0"
    assert manifest_of(repo)["kit_version"] == "0"


# --- upgrade ----------------------------------------------------------------------


def test_a_changed_payload_file_is_updated(kit, repo) -> None:
    kit.install(repo)
    kit.write(GATE, "print('gate v2')\n")
    report = kit.install(repo)
    assert report.status_of(GATE) == UPDATED
    assert read(repo, GATE) == "print('gate v2')\n"
    assert manifest_of(repo)["files"][GATE] == sha("print('gate v2')\n")
    assert report.status_of(STOP) == UNCHANGED
    assert report.status_of(MANIFEST) == UPDATED
    assert report.paths(UPDATED) == [GATE, MANIFEST]


def test_a_locally_modified_kit_file_is_skipped_until_forced(kit, repo) -> None:
    kit.install(repo)
    (repo.path / GATE).write_text("print('my edit')\n", encoding="utf-8")
    kit.write(GATE, "print('gate v2')\n")

    report = kit.install(repo)
    assert report.status_of(GATE) == SKIPPED
    assert read(repo, GATE) == "print('my edit')\n"
    # The old record stays, so the file is still recognised as modified next time.
    assert manifest_of(repo)["files"][GATE] == sha(KIT_FILES[GATE])
    assert kit.install(repo).status_of(GATE) == SKIPPED

    forced = kit.install(repo, force=True)
    assert forced.status_of(GATE) == UPDATED
    assert read(repo, GATE) == "print('gate v2')\n"
    assert manifest_of(repo)["files"][GATE] == sha("print('gate v2')\n")
    assert kit.install(repo).status_of(GATE) == UNCHANGED


def test_a_checkout_with_crlf_line_endings_is_not_a_local_modification(kit, repo) -> None:
    # What a Windows clone with core.autocrlf looks like: every text file has CRLF line endings.
    kit.install(repo)
    for path in (GATE, STOP, MANIFEST):
        target = repo.path / path
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    before = snapshot(repo.path)

    unchanged = kit.install(repo)
    assert unchanged.changed is False
    assert snapshot(repo.path) == before  # not rewritten either: the checkout keeps its line endings

    kit.write(GATE, "print('gate v2')\n")
    upgraded = kit.install(repo)
    assert upgraded.status_of(GATE) == UPDATED  # recognised as untouched kit content, so it is upgraded
    assert upgraded.status_of(STOP) == UNCHANGED
    assert (repo.path / STOP).read_bytes() == KIT_FILES[STOP].replace("\n", "\r\n").encode()
    assert kit.uninstall(repo).status_of(STOP) == REMOVED


def test_a_local_edit_is_skipped_even_when_the_payload_did_not_change(kit, repo) -> None:
    kit.install(repo)
    (repo.path / GATE).write_text("print('my edit')\n", encoding="utf-8")
    assert kit.install(repo).status_of(GATE) == SKIPPED
    assert read(repo, GATE) == "print('my edit')\n"


def test_a_file_that_was_there_before_the_kit_is_not_clobbered(kit, repo) -> None:
    workflow = ".github/workflows/claude-review.yml"
    repo.write(workflow, "name: mine\n")
    report = kit.install(repo)
    assert report.status_of(workflow) == SKIPPED
    assert read(repo, workflow) == "name: mine\n"
    assert workflow not in manifest_of(repo)["files"]  # never installed, so never removed either

    forced = kit.install(repo, force=True)
    assert forced.status_of(workflow) == UPDATED
    assert read(repo, workflow) == KIT_FILES[workflow]
    assert manifest_of(repo)["files"][workflow] == sha(KIT_FILES[workflow])


def test_an_identical_file_that_was_there_before_is_adopted(kit, repo) -> None:
    repo.write(GATE, KIT_FILES[GATE])
    report = kit.install(repo)
    assert report.status_of(GATE) == UNCHANGED
    assert manifest_of(repo)["files"][GATE] == sha(KIT_FILES[GATE])


def test_a_deleted_kit_file_is_restored(kit, repo) -> None:
    kit.install(repo)
    (repo.path / GATE).unlink()
    assert kit.install(repo).status_of(GATE) == CREATED
    assert read(repo, GATE) == KIT_FILES[GATE]


def test_a_file_dropped_from_the_payload_is_removed_when_unmodified(kit, repo) -> None:
    kit.install(repo)
    kit.remove(STOP)
    report = kit.install(repo)
    assert report.status_of(STOP) == REMOVED
    assert not (repo.path / STOP).exists()
    assert STOP not in manifest_of(repo)["files"]
    assert (repo.path / GATE).exists()
    assert kit.install(repo).status_of(STOP) is None  # nothing left to say about it


def test_a_dropped_file_with_local_changes_is_kept_until_forced(kit, repo) -> None:
    kit.install(repo)
    (repo.path / STOP).write_text("print('my stop')\n", encoding="utf-8")
    kit.remove(STOP)

    report = kit.install(repo)
    assert report.status_of(STOP) == SKIPPED
    assert read(repo, STOP) == "print('my stop')\n"
    assert manifest_of(repo)["files"][STOP] == sha(KIT_FILES[STOP])

    forced = kit.install(repo, force=True)
    assert forced.status_of(STOP) == REMOVED
    assert not (repo.path / STOP).exists()
    assert STOP not in manifest_of(repo)["files"]


def test_a_dropped_file_the_user_already_deleted_is_forgotten(kit, repo) -> None:
    kit.install(repo)
    (repo.path / STOP).unlink()
    kit.remove(STOP)
    report = kit.install(repo)
    assert report.status_of(STOP) is None
    assert STOP not in manifest_of(repo)["files"]


def test_dropping_the_last_file_of_a_kit_directory_removes_the_directory(kit, repo) -> None:
    kit.install(repo)
    kit.remove(".claude/skills/bug-finding/SKILL.md")
    kit.install(repo)
    assert not (repo.path / ".claude/skills/bug-finding").exists()
    assert (repo.path / ".claude/skills/pr-review/SKILL.md").exists()


def test_a_manifest_cannot_make_an_upgrade_delete_user_owned_or_outside_files(kit, repo, tmp_path: Path) -> None:
    kit.install(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("precious\n", encoding="utf-8")
    repo.write("src/app.py", "print('app')\n")
    manifest = manifest_of(repo)
    manifest["files"]["REVIEW.md"] = sha(USER_FILES["REVIEW.md"])  # user-owned
    manifest["files"]["../outside.txt"] = sha("precious\n")  # outside the target
    manifest["files"][".git/HEAD"] = hashlib.sha256((repo.path / ".git/HEAD").read_bytes()).hexdigest()
    (repo.path / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    report = kit.install(repo)
    assert report.ok
    assert (repo.path / "REVIEW.md").exists() and outside.exists() and (repo.path / ".git/HEAD").exists()
    assert set(manifest_of(repo)["files"]) == set(KIT_FILES)
    assert any("../outside.txt" in warning for warning in report.warnings)


def test_a_corrupt_manifest_is_replaced_and_existing_edits_are_respected(kit, repo) -> None:
    kit.install(repo)
    (repo.path / GATE).write_text("print('my edit')\n", encoding="utf-8")
    (repo.path / MANIFEST).write_text("{broken", encoding="utf-8")
    report = kit.install(repo)
    assert report.status_of(GATE) == SKIPPED  # without a record, a differing file counts as modified
    assert report.status_of(STOP) == UNCHANGED
    assert any("manifest" in warning for warning in report.warnings)
    files = manifest_of(repo)["files"]
    assert GATE not in files and files[STOP] == sha(KIT_FILES[STOP])


# --- create-if-absent ---------------------------------------------------------------


def test_user_owned_files_are_never_overwritten(kit, repo) -> None:
    repo.write("REVIEW.md", "# my rules\n")
    report = kit.install(repo)
    assert report.status_of("REVIEW.md") == KEPT
    assert read(repo, "REVIEW.md") == "# my rules\n"
    assert "REVIEW.md" not in manifest_of(repo)["files"]

    kit.write("REVIEW.md", "# a newer template\n")
    forced = kit.install(repo, force=True)
    assert forced.status_of("REVIEW.md") == KEPT
    assert read(repo, "REVIEW.md") == "# my rules\n"


def test_user_owned_files_edited_after_install_are_kept(kit, repo) -> None:
    kit.install(repo)
    for path in USER_FILES:
        (repo.path / path).write_text("edited by the user\n", encoding="utf-8")
        kit.write(path, "a newer template\n")
    report = kit.install(repo, force=True)
    for path in USER_FILES:
        assert report.status_of(path) == KEPT
        assert read(repo, path) == "edited by the user\n"


def test_a_missing_user_owned_file_is_created_again(kit, repo) -> None:
    kit.install(repo)
    (repo.path / "docs/reliability-checklist.md").unlink()
    assert kit.install(repo).status_of("docs/reliability-checklist.md") == CREATED
