"""`doctor`: does this installation still do what it promises? Local checks, no network."""

from __future__ import annotations

import json

import pytest

from doctor_kit import by_id, install_payload, write_config, write_manifest
from reviewkit.doctor.local import local_checks
from reviewkit.doctor.model import FAIL, OK, SKIP, WARN


@pytest.fixture
def installed(repo):
    install_payload(repo)
    return repo


def run(repo, env=None):
    return by_id(local_checks(repo.path, env or {}))


def test_a_fresh_committed_installation_is_healthy(installed):
    checks = local_checks(installed.path, {})
    problems = [(c.id, c.status, c.summary) for c in checks if c.status in (WARN, FAIL)]
    # The starter checklist ships with `___` origins on purpose: filling them in is the owner's first job.
    assert [p for p in problems if p[0] != "checklist"] == []
    assert {c.id for c in checks} >= {
        "python", "git", "config", "commands", "hooks", "ignore", "skills", "agents",
        "workflows", "rules", "checklist", "codeowners", "claude-md", "committed", "kit-files",
    }  # fmt: skip


def test_every_problem_says_how_to_fix_it(repo):
    install_payload(repo, config={"version": 1}, commit=False, manifest=False)
    (repo.path / "REVIEW.md").unlink()
    (repo.path / ".github" / "CODEOWNERS").unlink()
    for check in local_checks(repo.path, {}):
        if check.status in (WARN, FAIL):
            assert check.fix, check.id


def test_missing_config_fails_and_points_at_the_installer(installed):
    (installed.path / ".claude" / "reviewkit.json").unlink()
    check = run(installed)["config"]
    assert check.status == FAIL and "install" in check.fix


def test_unreadable_config_fails_but_the_other_checks_still_run(installed):
    installed.write(".claude/reviewkit.json", "{not json")
    checks = run(installed)
    assert checks["config"].status == FAIL
    assert checks["hooks"].status == OK


def test_unknown_config_keys_are_warnings(installed):
    write_config(installed, {"version": 1, "commands": {"test": "pytest -q"}, "bogus": True})
    check = run(installed)["config"]
    assert check.status == WARN and "bogus" in check.summary


def test_no_check_command_is_a_warning(installed):
    write_config(installed, {"version": 1})
    check = run(installed)["commands"]
    assert check.status == WARN and "test" in check.summary


def test_settings_without_the_hooks_fail(installed):
    installed.write(".claude/settings.json", json.dumps({"hooks": {}}))
    check = run(installed)["hooks"]
    assert check.status == FAIL and "commit gate" in check.summary and "Stop" in check.summary


def test_settings_that_are_not_json_fail(installed):
    installed.write(".claude/settings.json", "{")
    assert run(installed)["hooks"].status == FAIL


def test_a_missing_hook_script_fails(installed):
    (installed.path / ".claude" / "hooks" / "run_checks_on_stop.py").unlink()
    check = run(installed)["hooks"]
    assert check.status == FAIL and "run_checks_on_stop.py" in check.summary


def test_a_hook_script_that_does_not_compile_fails(installed):
    installed.write(".claude/hooks/verify_commit_gate.py", "def broken(:\n")
    check = run(installed)["hooks"]
    assert check.status == FAIL and "verify_commit_gate.py" in check.summary


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_disabled_hooks_are_reported(installed, name):
    path = installed.path / ".claude" / name
    data = json.loads(path.read_text()) if path.exists() else {}
    data["disableAllHooks"] = True
    path.write_text(json.dumps(data))
    check = run(installed)["hooks"]
    assert check.status == WARN and "disableAllHooks" in check.summary


def test_hooks_switched_off_by_the_environment_are_reported(installed):
    check = run(installed, {"REVIEWKIT_DISABLE": "1"})["hooks"]
    assert check.status == WARN and "REVIEWKIT_DISABLE" in check.summary


def test_state_directory_must_be_ignored(installed):
    installed.write(".gitignore", "")
    check = run(installed)["ignore"]
    assert check.status == WARN and ".claude/state/" in check.fix


@pytest.mark.parametrize(
    "relative, check_id",
    [(".claude/skills/pr-review/SKILL.md", "skills"), (".claude/agents/finding-verifier.md", "agents")],
)
def test_a_missing_primitive_fails(installed, relative, check_id):
    (installed.path / relative).unlink()
    check = run(installed)[check_id]
    assert (
        check.status == FAIL and relative.split("/")[-2 if check_id == "skills" else -1].split(".")[0] in check.summary
    )


def test_a_skill_whose_name_does_not_match_its_folder_fails(installed):
    path = installed.path / ".claude" / "skills" / "bug-finding" / "SKILL.md"
    path.write_text(path.read_text().replace("name: bug-finding", "name: bugfinding", 1))
    check = run(installed)["skills"]
    assert check.status == FAIL and "bug-finding" in check.summary


def test_the_review_workflow_is_required_and_the_others_are_optional(installed):
    workflows = installed.path / ".github" / "workflows"
    (workflows / "review-metrics.yml").unlink()
    check = run(installed)["workflows"]
    assert check.status == WARN and "review-metrics.yml" in check.summary
    (workflows / "claude-review.yml").unlink()
    check = run(installed)["workflows"]
    assert check.status == FAIL and "claude-review.yml" in check.summary


def test_a_workflow_that_asks_for_an_api_key_is_reported(installed):
    path = installed.path / ".github" / "workflows" / "claude.yml"
    path.write_text(path.read_text() + "\n# anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}\n")
    check = run(installed)["workflows"]
    assert check.status == WARN and "ANTHROPIC_API_KEY" in check.summary and "claude.yml" in check.summary


def test_missing_review_rules_are_a_warning(installed):
    (installed.path / "REVIEW.md").unlink()
    assert run(installed)["rules"].status == WARN


def test_oversized_review_rules_are_a_warning(installed):
    installed.write("REVIEW.md", "x" * 150_001)
    check = run(installed)["rules"]
    assert check.status == WARN and "150" in check.summary


def test_a_broken_checklist_fails_and_placeholders_warn(installed):
    assert run(installed)["checklist"].status == WARN  # the starter still has ___ origins
    installed.write("docs/reliability-checklist.md", "# Checklist\n\nno table here\n")
    assert run(installed)["checklist"].status == FAIL
    table = "| ID | Trigger | Check | Origin |\n|--|--|--|--|\n| RC-01 | A log line changes | No tokens are logged | PR #12 |\n"
    installed.write("docs/reliability-checklist.md", table)
    assert run(installed)["checklist"].status == OK


def test_missing_codeowners_is_a_warning(installed):
    (installed.path / ".github" / "CODEOWNERS").unlink()
    assert run(installed)["codeowners"].status == WARN


def test_codeowners_that_do_not_cover_the_review_rules_warn(installed):
    installed.write(".github/CODEOWNERS", "*.js @octo/web\n")
    check = run(installed)["codeowners"]
    assert check.status == WARN and ".claude/" in check.summary


def test_a_claude_md_without_the_contract_or_with_placeholders_warns(installed):
    installed.write("CLAUDE.md", "# Project\n")
    assert run(installed)["claude-md"].status == WARN
    installed.write("CLAUDE.md", "<!-- reviewkit:begin -->\n- Tests: `{{TEST_COMMAND}}`\n<!-- reviewkit:end -->\n")
    check = run(installed)["claude-md"]
    assert check.status == WARN and "{{TEST_COMMAND}}" in check.summary


def test_uncommitted_kit_files_are_reported_because_ci_reads_the_default_branch(repo):
    install_payload(repo, commit=False)
    check = run(repo)["committed"]
    assert check.status == WARN and "commit" in check.fix
    repo.commit("install")
    assert run(repo)["committed"].status == OK
    repo.write("REVIEW.md", "# changed\n")
    check = run(repo)["committed"]
    assert check.status == WARN and "REVIEW.md" in check.summary


def test_local_state_never_counts_as_uncommitted(installed):
    installed.write(".claude/state/gate.log", "{}\n")
    assert run(installed)["committed"].status == OK


def test_edited_and_missing_kit_files_are_told_apart(installed):
    assert run(installed)["kit-files"].status == OK
    installed.write(".claude/agents/bug-finder.md", "edited\n")
    check = run(installed)["kit-files"]
    assert check.status == WARN and "bug-finder.md" in check.summary
    (installed.path / ".claude" / "reviewkit" / "rk.py").unlink()
    check = run(installed)["kit-files"]
    assert check.status == FAIL and "rk.py" in check.summary


def test_line_ending_changes_are_not_local_edits(installed):
    path = installed.path / ".claude" / "agents" / "bug-finder.md"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert run(installed)["kit-files"].status == OK


def test_without_a_manifest_the_kit_file_check_is_skipped(repo):
    install_payload(repo, manifest=False)
    assert run(repo)["kit-files"].status == SKIP


def test_a_manifest_cannot_point_outside_the_repository(installed, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    document = write_manifest(installed)
    document["files"]["../outside.txt"] = "0" * 64
    document["files"]["/etc/hostname"] = "0" * 64
    installed.write(".claude/reviewkit/manifest.json", json.dumps(document))
    check = run(installed)["kit-files"]
    assert "outside.txt" not in check.summary and "hostname" not in check.summary


def test_outside_a_git_repository_the_git_check_fails_and_the_rest_still_run(tmp_path):
    checks = by_id(local_checks(tmp_path, {}))
    assert checks["git"].status == FAIL
    assert checks["config"].status == FAIL
    assert checks["committed"].status == SKIP
