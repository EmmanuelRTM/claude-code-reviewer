"""`rk issue intake`, `rk checklist lint` and command registration, end to end."""

from __future__ import annotations

import re
from typing import Any, Dict, Sequence

import pytest

from gates.gates_kit import Actions, calls, raises, run_cli
from reviewkit import cli, cmd_gates, issues

TABLE = "| ID | Trigger (readable from a diff) | Check | Origin |\n| --- | --- | --- | --- |\n"
CLEAN = "# Reliability checklist\n\n" + TABLE + "| RC-01 | A new HTTP call | It sets a timeout | PR 12 |\n"
BROKEN = TABLE + "| RC-01 | t | c | o |\n| RC-1 | t | c | o |\n| RC-01 | t | c | ___ |\n| RC-04 | t | c |\n"
FORM = "### Acceptance criteria\n\n{criteria}\n\n### Risk tier\n\n{risk}\n"


@pytest.fixture
def actions(tmp_path, monkeypatch) -> Actions:
    """A fake workflow step: environment, event file, output files and a project directory as cwd."""
    return Actions(tmp_path, monkeypatch)


@pytest.fixture
def gh(actions, github):
    """The in-memory GitHub, handed to the gates commands in place of a real client."""
    return actions.use_github(github)


def issue_event(action: str, body: str, labels: Sequence[str] = (), **extra: Any) -> Dict[str, Any]:
    issue: Dict[str, Any] = {"number": 3, "body": body, "labels": [{"name": name} for name in labels]}
    issue.update(extra)
    return {"action": action, "issue": issue}


def route_issue(gh: Any) -> None:
    gh.on("POST", r"/repos/o/r/labels", {})
    gh.on("POST", r"/repos/o/r/issues/3/labels", [])
    gh.on("DELETE", r"/repos/o/r/issues/3/labels/.+", None)
    gh.on("GET", r"/repos/o/r/issues/3/comments", [])
    gh.on("POST", r"/repos/o/r/issues/3/comments", {"id": 1})


# -- issue intake -------------------------------------------------------------


@pytest.mark.parametrize("action", ["opened", "edited"])
def test_intake_labels_a_complete_issue(gh, actions, capsys, action: str) -> None:
    route_issue(gh)
    actions.event("issues", issue_event(action, FORM.format(criteria="- [ ] Exports a CSV", risk="High")))
    assert run_cli(["issue", "intake"]) == 0
    assert calls(gh) == [("POST", "/repos/o/r/labels"), ("POST", "/repos/o/r/issues/3/labels")]
    assert gh.calls[1]["body"] == {"labels": ["risk:high"]}
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1 and "#3" in out


def test_intake_asks_for_missing_acceptance_criteria(gh, actions) -> None:
    route_issue(gh)
    actions.event("issues", issue_event("opened", FORM.format(criteria="_No response_", risk="Low")))
    assert run_cli(["issue", "intake"]) == 0
    assert calls(gh) == [
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues/3/labels"),
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues/3/labels"),
        ("GET", "/repos/o/r/issues/3/comments"),
        ("POST", "/repos/o/r/issues/3/comments"),
    ]
    assert gh.calls[1]["body"] == {"labels": ["risk:low"]}
    assert gh.calls[3]["body"] == {"labels": ["needs-acceptance-criteria"]}
    assert gh.calls[5]["body"] == {"body": issues.INTAKE_COMMENT}


def test_intake_clears_the_label_once_criteria_exist(gh, actions) -> None:
    route_issue(gh)
    body = FORM.format(criteria="- [ ] Exports a CSV", risk="Medium")
    actions.event("issues", issue_event("edited", body, ["needs-acceptance-criteria", "risk:medium"]))
    assert run_cli(["issue", "intake"]) == 0
    assert calls(gh) == [("DELETE", "/repos/o/r/issues/3/labels/needs-acceptance-criteria")]


@pytest.mark.parametrize(
    ("name", "event"),
    [
        ("issues", issue_event("closed", "x")),
        ("issues", issue_event("labeled", "x")),
        ("issues", issue_event("opened", "x", pull_request={"url": "https://api.github.test/pulls/3"})),
        ("issues", {"action": "opened"}),
        ("issue_comment", issue_event("opened", "x")),
        ("pull_request_target", issue_event("opened", "x")),
        ("", {}),
    ],
)
def test_intake_ignores_everything_but_opened_and_edited_issues(gh, actions, capsys, name: str, event) -> None:
    actions.event(name, event)
    assert run_cli(["issue", "intake"]) == 0
    assert gh.calls == []
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_intake_never_prints_what_the_issue_says(gh, actions, capsys) -> None:
    route_issue(gh)
    token = "ZXQ-UNTRUSTED-7731"
    actions.event("issues", issue_event("opened", f"{token}\n\n### Risk tier\n\nHigh {token}", title=token))
    assert run_cli(["issue", "intake"]) == 0
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err + actions.summary() + repr(actions.outputs())
    assert all(token not in repr(call["body"]) for call in gh.calls)


def test_intake_reports_github_failures(gh, actions, capsys) -> None:
    route_issue(gh)
    gh.on("POST", r"/repos/o/r/issues/3/labels", raises(403))
    actions.event("issues", issue_event("opened", FORM.format(criteria="- [ ] x1", risk="High")))
    assert run_cli(["issue", "intake"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1 and "403" in err


def test_intake_needs_a_repository(gh, actions, capsys, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY")
    actions.event("issues", issue_event("opened", "x"))
    assert run_cli(["issue", "intake"]) == 1
    assert gh.calls == [] and capsys.readouterr().err.startswith("error: ")


# -- checklist lint -----------------------------------------------------------


def test_lint_reads_the_configured_checklist_by_default(actions, capsys) -> None:
    actions.write("docs/reliability-checklist.md", CLEAN)
    assert run_cli(["checklist", "lint"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip().splitlines() == ["docs/reliability-checklist.md: 1 item, 0 errors, 0 warnings"]


def test_lint_follows_the_checklist_path_in_the_configuration(actions, capsys) -> None:
    actions.config({"review": {"checklist_path": "ops/checks.md"}})
    actions.write("ops/checks.md", CLEAN)
    assert run_cli(["checklist", "lint"]) == 0
    assert "ops/checks.md: 1 item" in capsys.readouterr().out


def test_lint_prints_each_issue_and_fails_on_errors(actions, capsys) -> None:
    path = actions.write("elsewhere/list.md", BROKEN)
    assert run_cli(["checklist", "lint", str(path)]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert [re.match(r"(error|warning): line (\d+): \S", line) is not None for line in lines[:-1]] == [True] * 4
    assert [line.split(":")[0] + line.split(":")[1] for line in lines[:-1]] == [
        "error line 4",
        "error line 5",
        "warning line 5",
        "error line 6",
    ]
    assert lines[-1].endswith(": 3 items, 3 errors, 1 warning")


def test_lint_passes_with_warnings_only(actions, capsys) -> None:
    path = actions.write("list.md", TABLE + "| RC-01 | t | c | PR #___ |\n")
    assert run_cli(["checklist", "lint", str(path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("warning: line 3: ")
    assert lines[-1].endswith(": 1 item, 0 errors, 1 warning")


def test_lint_treats_a_missing_file_as_an_error(actions, capsys) -> None:
    assert run_cli(["checklist", "lint"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ") and "docs/reliability-checklist.md" in captured.err
    assert len(captured.err.strip().splitlines()) == 1
    assert run_cli(["checklist", "lint", "no/such/file.md"]) == 1
    assert "no/such/file.md" in capsys.readouterr().err


def test_lint_treats_an_unreadable_file_as_an_error(actions, capsys) -> None:
    (actions.project / "binary.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    assert run_cli(["checklist", "lint", "binary.md"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert run_cli(["checklist", "lint", "."]) == 1


def test_lint_without_a_table_fails(actions, capsys) -> None:
    actions.write("docs/reliability-checklist.md", "# Reliability checklist\n\nComing soon.\n")
    assert run_cli(["checklist", "lint"]) == 1
    assert capsys.readouterr().out.startswith("error: line 1: ")


def test_lint_reports_a_broken_configuration(actions, capsys) -> None:
    actions.config({"review": {"checklist_path": 7}})
    assert run_cli(["checklist", "lint"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "checklist_path" in err


def test_lint_with_an_explicit_path_needs_no_configuration_and_no_github(actions, capsys) -> None:
    actions.config("{ not json")
    path = actions.write("list.md", CLEAN)
    assert run_cli(["checklist", "lint", str(path)]) == 0


# -- registration -------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [["risk", "evaluate"], ["issue", "intake"], ["checklist", "lint"], ["metrics", "report"]],
)
def test_every_command_is_registered_with_a_handler(argv) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    assert callable(args.handler)
    assert args.handler.__module__ == cmd_gates.__name__


@pytest.mark.parametrize("group", ["risk", "issue", "checklist", "metrics"])
def test_a_group_without_a_subcommand_is_a_usage_error(group: str, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_cli([group])
    assert exit_info.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_metrics_report_options_parse() -> None:
    args = cli.build_parser().parse_args(["metrics", "report", "--days", "7", "--post-issue", "--repo", "o/r"])
    assert (args.days, args.post_issue, args.repo) == (7, True, "o/r")
    defaults = cli.build_parser().parse_args(["metrics", "report"])
    assert (defaults.days, defaults.post_issue, defaults.repo) == (30, False, None)
