"""Install-level behaviour of the generated config, the settings merge and the managed blocks."""

from __future__ import annotations

import json

import pytest
from installer_kit import FRAGMENT, snapshot

from installer.errors import InstallError
from installer.report import CREATED, ERROR, MERGED, UNCHANGED, UPDATED
from reviewkit.config import Config, config_to_dict

pytest_plugins = ("installer_fixtures",)

CONFIG = ".claude/reviewkit.json"
SETTINGS = ".claude/settings.json"
USER_SETTINGS = {
    "permissions": {"allow": ["Bash(npm test)"]},
    "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "./fmt.sh"}]}]},
    "model": "opus",
}


def read(repo, relative: str) -> str:
    return (repo.path / relative).read_text(encoding="utf-8")


def config_of(repo) -> dict:
    return json.loads(read(repo, CONFIG))


def detail_of(report, path: str) -> str:
    return next(entry.detail for entry in report.entries if entry.path == path)


# --- generated configuration -------------------------------------------------------


def test_detection_seeds_a_new_configuration(kit, repo) -> None:
    repo.write("go.mod", "module demo\n")
    kit.install(repo)
    commands = config_of(repo)["commands"]
    assert (commands["test"], commands["lint"], commands["build"]) == (
        "go test ./...",
        "go vet ./...",
        "go build ./...",
    )
    assert "- Test: `go test ./...`" in read(repo, "CLAUDE.md")


def test_detection_can_be_switched_off(kit, repo) -> None:
    repo.write("go.mod", "module demo\n")
    kit.install(repo, detect=False)
    assert config_of(repo) == config_to_dict(Config())
    assert "- Test: not configured (set commands.test in .claude/reviewkit.json)" in read(repo, "CLAUDE.md")


def test_the_default_branch_is_detected_from_the_remote(kit, repo) -> None:
    repo.git("update-ref", "refs/remotes/origin/trunk", "HEAD")
    repo.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    kit.install(repo)
    assert config_of(repo)["default_branch"] == "trunk"


def test_explicit_values_win_over_detection(kit, repo) -> None:
    repo.write("go.mod", "module demo\n")
    kit.install(repo, overrides={"commands": {"test": "make check"}, "default_branch": "develop"})
    config = config_of(repo)
    assert config["commands"]["test"] == "make check"
    assert config["commands"]["lint"] == "go vet ./..."
    assert config["default_branch"] == "develop"


def test_an_existing_configuration_is_preserved(kit, repo) -> None:
    kit.install(repo)
    repo.write("go.mod", "module demo\n")  # appears later: must not be detected into the user's file
    before = read(repo, CONFIG)
    report = kit.install(repo)
    assert report.status_of(CONFIG) == UNCHANGED
    assert read(repo, CONFIG) == before


def test_overrides_change_only_their_keys_in_an_existing_configuration(kit, repo) -> None:
    repo.write(CONFIG, '{\n    "commands": {"test": "mine"},\n    "note": "hand written"\n}\n')
    report = kit.install(repo, overrides={"gate": {"mode": "enforce"}})
    assert report.status_of(CONFIG) == UPDATED
    assert config_of(repo) == {"commands": {"test": "mine"}, "note": "hand written", "gate": {"mode": "enforce"}}
    assert any("note" in warning for warning in report.warnings)  # unknown key: kept, and mentioned
    assert "- Test: `mine`" in read(repo, "CLAUDE.md")


def test_a_hand_formatted_configuration_is_not_reformatted(kit, repo) -> None:
    text = '{"commands":{"test":"mine"}}'
    repo.write(CONFIG, text)
    assert kit.install(repo).status_of(CONFIG) == UNCHANGED
    assert read(repo, CONFIG) == text


@pytest.mark.parametrize("corrupt", ["{broken", "[]", '{"gate": {"mode": "bogus"}}'])
def test_an_unusable_existing_configuration_stops_the_run_before_any_write(kit, repo, corrupt: str) -> None:
    repo.write(CONFIG, corrupt)
    before = snapshot(repo.path)
    with pytest.raises(InstallError):
        kit.install(repo, force=True)
    assert snapshot(repo.path) == before


def test_an_invalid_override_stops_the_run_before_any_write(kit, repo) -> None:
    before = snapshot(repo.path)
    with pytest.raises(InstallError, match="gate.mode"):
        kit.install(repo, overrides={"gate": {"mode": "bogus"}})
    assert snapshot(repo.path) == before


def test_changing_a_command_later_updates_the_block_in_place(kit, repo) -> None:
    repo.write("CLAUDE.md", "# Project\n\nIntro.\n")
    kit.install(repo)
    with open(repo.path / "CLAUDE.md", "a", encoding="utf-8") as handle:
        handle.write("\n## My own notes\n")
    report = kit.install(repo, overrides={"commands": {"test": "make check"}})
    assert report.status_of("CLAUDE.md") == MERGED
    text = read(repo, "CLAUDE.md")
    assert text.startswith("# Project\n\nIntro.\n\n<!-- reviewkit:begin -->\n")
    assert text.endswith("<!-- reviewkit:end -->\n\n## My own notes\n")
    assert "- Test: `make check`" in text
    assert text.count("<!-- reviewkit:begin -->") == 1


# --- code owners --------------------------------------------------------------------


def test_without_owners_the_rules_are_commented_out_and_a_warning_is_given(kit, repo) -> None:
    report = kit.install(repo)
    lines = read(repo, ".github/CODEOWNERS").splitlines()
    assert lines[0] == "# reviewkit:begin" and lines[-1] == "# reviewkit:end"
    assert "# /REVIEW.md                      @your-team" in lines
    assert all(line.startswith("#") for line in lines)
    assert any("owners" in warning and "not set" in warning for warning in report.warnings)


def test_with_owners_the_rules_are_active(kit, repo) -> None:
    report = kit.install(repo, overrides={"owners": ["@org/leads", "@alice"]})
    lines = read(repo, ".github/CODEOWNERS").splitlines()
    assert lines[2:-1] == [
        "/REVIEW.md                      @org/leads @alice",
        "/docs/reliability-checklist.md  @org/leads @alice",
        "/CLAUDE.md                      @org/leads @alice",
        "/.claude/                       @org/leads @alice",
        "/.github/                       @org/leads @alice",
    ]
    assert not [warning for warning in report.warnings if "owners" in warning]


def test_setting_owners_later_activates_the_rules(kit, repo) -> None:
    kit.install(repo)
    report = kit.install(repo, overrides={"owners": ["@org/leads"]})
    assert report.status_of(".github/CODEOWNERS") == MERGED
    assert "/.claude/                       @org/leads" in read(repo, ".github/CODEOWNERS")


def test_an_invalid_owner_stops_the_run(kit, repo) -> None:
    before = snapshot(repo.path)
    with pytest.raises(InstallError, match="owners"):
        kit.install(repo, overrides={"owners": ["alice"]})
    assert snapshot(repo.path) == before


def test_an_existing_codeowners_file_keeps_its_rules(kit, repo) -> None:
    repo.write(".github/CODEOWNERS", "*.js @frontend\n")
    report = kit.install(repo, overrides={"owners": ["@org/leads"]})
    assert report.status_of(".github/CODEOWNERS") == MERGED
    assert read(repo, ".github/CODEOWNERS").startswith("*.js @frontend\n\n# reviewkit:begin\n")


@pytest.mark.parametrize("location", ["CODEOWNERS", "docs/CODEOWNERS"])
def test_the_block_joins_a_codeowners_file_outside_dot_github(kit, repo, location: str) -> None:
    # GitHub reads only the first CODEOWNERS it finds (.github/, then the root, then docs/).
    # Creating .github/CODEOWNERS here would silently switch off the existing rules.
    repo.write(location, "* @everyone\n")
    report = kit.install(repo, overrides={"owners": ["@org/leads"]})
    assert report.status_of(location) == MERGED
    assert read(repo, location).startswith("* @everyone\n\n# reviewkit:begin\n")
    assert not (repo.path / ".github/CODEOWNERS").exists()
    assert kit.install(repo, overrides={"owners": ["@org/leads"]}).status_of(location) == UNCHANGED


# --- settings ------------------------------------------------------------------------


def test_hooks_are_merged_into_existing_settings(kit, repo) -> None:
    repo.write(SETTINGS, json.dumps(USER_SETTINGS))
    report = kit.install(repo)
    assert report.status_of(SETTINGS) == MERGED
    merged = json.loads(read(repo, SETTINGS))
    assert merged["permissions"] == USER_SETTINGS["permissions"]
    assert merged["model"] == "opus"
    assert merged["hooks"]["PreToolUse"] == USER_SETTINGS["hooks"]["PreToolUse"] + FRAGMENT["hooks"]["PreToolUse"]
    assert merged["hooks"]["Stop"] == FRAGMENT["hooks"]["Stop"]
    assert read(repo, SETTINGS).endswith("}\n")
    assert kit.install(repo).status_of(SETTINGS) == UNCHANGED


def test_a_new_hook_in_a_newer_kit_is_added_without_duplicating_the_old_ones(kit, repo) -> None:
    kit.install(repo)
    newer = json.loads(json.dumps(FRAGMENT))
    newer["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": "python3 -I .claude/hooks/start.py"}]}]
    kit.write(SETTINGS, json.dumps(newer))
    assert kit.install(repo).status_of(SETTINGS) == MERGED
    assert json.loads(read(repo, SETTINGS)) == newer


@pytest.mark.parametrize("corrupt", ["{broken", "[]", '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
def test_a_corrupt_settings_file_is_left_untouched(kit, repo, corrupt: str) -> None:
    repo.write(SETTINGS, corrupt)
    report = kit.install(repo, force=True)
    assert not report.ok
    assert report.status_of(SETTINGS) == ERROR
    assert "left untouched" in detail_of(report, SETTINGS)
    assert read(repo, SETTINGS) == corrupt
    # Only that step was abandoned.
    assert (repo.path / ".claude/hooks/gate.py").exists()
    assert report.status_of("CLAUDE.md") == CREATED


def test_a_payload_without_a_settings_fragment_leaves_settings_alone(kit, repo) -> None:
    kit.remove(SETTINGS)
    report = kit.install(repo)
    assert report.status_of(SETTINGS) is None
    assert not (repo.path / SETTINGS).exists()


def test_fragment_keys_other_than_hooks_are_reported_not_merged(kit, repo) -> None:
    kit.write(SETTINGS, json.dumps({**FRAGMENT, "permissions": {"allow": ["Bash(*)"]}}))
    report = kit.install(repo)
    assert json.loads(read(repo, SETTINGS)) == FRAGMENT
    assert any("permissions" in warning for warning in report.warnings)


def test_a_malformed_fragment_in_the_kit_stops_the_run(kit, repo) -> None:
    kit.write(SETTINGS, '{"hooks": "nope"}')
    before = snapshot(repo.path)
    with pytest.raises(InstallError, match="fragment"):
        kit.install(repo)
    assert snapshot(repo.path) == before


# --- managed blocks --------------------------------------------------------------------


@pytest.mark.parametrize(
    "damaged",
    [
        "# Project\n\n<!-- reviewkit:begin -->\nhalf a block\n",
        "# Project\n\nrest of a block\n<!-- reviewkit:end -->\n",
        "<!-- reviewkit:begin -->\na\n<!-- reviewkit:end -->\n<!-- reviewkit:begin -->\nb\n<!-- reviewkit:end -->\n",
    ],
)
def test_damaged_markers_leave_the_file_untouched(kit, repo, damaged: str) -> None:
    repo.write("CLAUDE.md", damaged)
    report = kit.install(repo)
    assert not report.ok
    assert report.status_of("CLAUDE.md") == ERROR
    assert "marker" in detail_of(report, "CLAUDE.md")
    assert read(repo, "CLAUDE.md") == damaged
    assert report.status_of(".gitignore") == MERGED  # the other blocks still went in


def test_a_file_that_is_not_utf8_is_left_untouched(kit, repo) -> None:
    (repo.path / "CLAUDE.md").write_bytes(b"\xff\xfe\x00binary")
    report = kit.install(repo)
    assert report.status_of("CLAUDE.md") == ERROR
    assert (repo.path / "CLAUDE.md").read_bytes() == b"\xff\xfe\x00binary"


def test_a_broken_template_stops_the_run(kit, repo) -> None:
    kit.write_block("CLAUDE.md.block", "<!-- reviewkit:begin -->\n{{NOBODY_FILLS_THIS}}\n<!-- reviewkit:end -->\n")
    before = snapshot(repo.path)
    with pytest.raises(InstallError, match="NOBODY_FILLS_THIS"):
        kit.install(repo)
    assert snapshot(repo.path) == before


def test_a_template_without_markers_stops_the_run(kit, repo) -> None:
    kit.write_block("gitignore.block", ".claude/state/\n")
    with pytest.raises(InstallError, match=r"gitignore\.block"):
        kit.install(repo)
    assert not (repo.path / "CLAUDE.md").exists()
