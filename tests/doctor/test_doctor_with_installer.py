"""The real installer followed by the real health check: the two must agree on what healthy means."""

from __future__ import annotations

import json

import pytest

from installer.cli import main as install
from installer.manifest import content_hash as installer_hash
from reviewkit.config import Config, config_to_dict
from reviewkit.doctor.kitfiles import MANIFEST_PATH, content_hash
from reviewkit.doctor.local import local_checks
from reviewkit.doctor.model import FAIL, OK, WARN


@pytest.fixture
def installed(repo):
    repo.write("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    repo.write("tests/test_ok.py", "def test_ok():\n    assert True\n")
    repo.commit("a project with tests")
    assert install(["--target", str(repo.path), "--set", "owners=@octo/maintainers"]) == 0
    return repo


def statuses(repo):
    return {check.id: check for check in local_checks(repo.path, {})}


def test_a_fresh_installation_has_no_failures_and_only_the_expected_warnings(installed):
    checks = local_checks(installed.path, {})
    assert [c.id for c in checks if c.status == FAIL] == []
    # Two warnings are the owner's first jobs: commit the files, and replace the checklist's placeholder origins.
    assert sorted(c.id for c in checks if c.status == WARN) == ["checklist", "committed"]


def test_once_committed_only_the_starter_checklist_still_asks_for_attention(installed):
    installed.commit("install the review kit")
    checks = local_checks(installed.path, {})
    assert sorted(c.id for c in checks if c.status in (WARN, FAIL)) == ["checklist"]


def test_the_health_check_reads_the_manifest_the_installer_writes(installed):
    assert statuses(installed)["kit-files"].status == OK
    manifest = json.loads((installed.path / MANIFEST_PATH).read_text())
    assert len(manifest["files"]) > 40
    installed.write(".claude/agents/bug-finder.md", "edited locally\n")
    check = statuses(installed)["kit-files"]
    assert check.status == WARN and "bug-finder.md" in check.summary


@pytest.mark.parametrize("data", [b"", b"plain\n", b"windows\r\nlines\r\n", b"binary\0\r\n", "ünïcode\n".encode()])
def test_both_sides_hash_content_the_same_way(data):
    assert content_hash(data) == installer_hash(data)


def test_the_generated_configuration_is_the_library_default_plus_what_was_detected(installed):
    generated = json.loads((installed.path / ".claude" / "reviewkit.json").read_text())
    expected = config_to_dict(Config())
    expected["owners"] = ["@octo/maintainers"]
    expected["commands"]["test"] = generated["commands"]["test"]
    assert generated["commands"]["test"]  # detected from the project
    assert generated == expected


def test_installing_twice_changes_nothing(installed):
    installed.commit("install the review kit")
    assert install(["--target", str(installed.path), "--set", "owners=@octo/maintainers"]) == 0
    assert installed.git("status", "--porcelain").strip() == ""
