"""`rk metrics report` end to end against the fake GitHub."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import pytest

from gates.gates_kit import Actions, calls, commit, finding, raises, review, run_cli, user
from reviewkit import cmd_gates, labels, markers, metrics

TOKEN = "ZXQ-UNTRUSTED-7731"
TITLE = "Review metrics: 2026-02-01 to 2026-03-03"
CHECKLIST = (
    "| ID | Trigger (readable from a diff) | Check | Origin |\n| --- | --- | --- | --- |\n"
    "| RC-01 | A new HTTP call | It sets a timeout | PR 12 |\n"
    "| RC-02 | A retry loop | It has a limit | PR 31 |\n"
)


@pytest.fixture
def actions(tmp_path, monkeypatch) -> Actions:
    """A fake workflow step: environment, event file, output files and a project directory as cwd."""
    return Actions(tmp_path, monkeypatch)


@pytest.fixture
def gh(actions, github):
    """The in-memory GitHub, handed to the gates commands in place of a real client."""
    return actions.use_github(github)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_gates, "_today", lambda: date(2026, 3, 3))


def route_window(gh: Any, numbers: List[int]) -> None:
    """A window whose merged pull requests all look alike, hostile text included."""
    items = [{"number": n, "title": TOKEN, "labels": [{"name": "review-round:2"}, {"name": TOKEN}]} for n in numbers]
    hostile_finding = {
        "user": user("github-actions[bot]", "Bot"),
        "body": f"{TOKEN}\n{markers.encode('finding', {'id': TOKEN, 'sev': TOKEN, 'cid': TOKEN, 'round': 1})}",
        "reactions": {"+1": 1},
    }
    gh.on("GET", r"/search/issues", {"total_count": len(items), "items": items})
    gh.on(
        "GET",
        r"/repos/[^/]+/[^/]+/pulls/(\d+)",
        lambda match, params, body: {
            "number": int(match.group(1)),
            "title": f"{TOKEN} <script>",
            "body": TOKEN,
            "created_at": "2026-02-10T08:00:00Z",
            "merged_at": "2026-02-11T08:00:00Z",
            "user": user(TOKEN),
        },
    )
    gh.on("GET", r"/repos/[^/]+/[^/]+/pulls/\d+/reviews", [review("alice", at="2026-02-10T10:00:00Z")])
    gh.on("GET", r"/repos/[^/]+/[^/]+/pulls/\d+/comments", [finding("nit", "RC-01", {"+1": 1}), hostile_finding])
    gh.on("GET", r"/repos/[^/]+/[^/]+/pulls/\d+/commits", [commit(f"{TOKEN}\n\nVerified-By: none ({TOKEN})")])
    gh.on("POST", r"/repos/[^/]+/[^/]+/labels", {})
    gh.on("POST", r"/repos/[^/]+/[^/]+/issues", {"number": 55, "html_url": "https://github.test/o/r/issues/55"})


def query(gh: Any) -> str:
    return gh.calls[0]["params"]["q"]


def test_report_is_printed_and_nothing_is_posted_by_default(gh, actions, capsys) -> None:
    route_window(gh, [5, 9])
    actions.write("docs/reliability-checklist.md", CHECKLIST)
    assert run_cli(["metrics", "report"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"# {TITLE}\n")
    assert query(gh) == "repo:o/r is:pr is:merged merged:>=2026-02-01"
    assert [call for call in calls(gh) if call[0] != "GET"] == []
    assert "| Merged pull requests | 2 |" in out
    assert "| Mean rounds to pass | 2.00 |" in out
    assert "| RC-01 | 2 | yes |" in out
    assert "| Opted out | 2 (100.0%) |" in out
    assert "| Median hours to the first human approval | 2.0 |" in out
    # RC-02 produced no findings in the window; RC-01 did.
    assert "\n- RC-02\n" in out and "\n- RC-01\n" not in out
    assert actions.summary() == out


def test_post_issue_creates_a_labelled_issue_holding_the_report(gh, actions, capsys) -> None:
    route_window(gh, [5])
    assert run_cli(["metrics", "report", "--post-issue"]) == 0
    captured = capsys.readouterr()
    assert calls(gh)[-2:] == [("POST", "/repos/o/r/labels"), ("POST", "/repos/o/r/issues")]
    spec = labels.KIT_LABELS["review-metrics"]
    assert gh.calls[-2]["body"] == {"name": "review-metrics", "color": spec.color, "description": spec.description}
    assert gh.calls[-1]["body"] == {"title": TITLE, "body": captured.out, "labels": ["review-metrics"]}
    assert "#55" in captured.err


def test_an_existing_label_does_not_stop_the_post(gh, actions) -> None:
    route_window(gh, [5])
    gh.on("POST", r"/repos/o/r/labels", raises(422))
    assert run_cli(["metrics", "report", "--post-issue"]) == 0
    assert calls(gh)[-1] == ("POST", "/repos/o/r/issues")


def test_nothing_people_wrote_ends_up_in_the_report(gh, actions, capsys) -> None:
    route_window(gh, [5, 9])
    assert run_cli(["metrics", "report", "--post-issue"]) == 0
    captured = capsys.readouterr()
    assert TOKEN not in captured.out and TOKEN not in captured.err
    assert "<script>" not in captured.out
    assert TOKEN not in repr(gh.calls[-1]["body"])


@pytest.mark.parametrize(
    ("argv", "since"),
    [
        ([], "2026-02-01"),
        (["--days", "30"], "2026-02-01"),
        (["--days", "7"], "2026-02-24"),
        (["--days", "1"], "2026-03-02"),
        (["--days", " 14 "], "2026-02-17"),
        # A scheduled run has no workflow input; the empty string means the default.
        (["--days", ""], "2026-02-01"),
        (["--days=90"], "2025-12-03"),
    ],
)
def test_days_sets_the_start_of_the_window(gh, actions, capsys, argv, since: str) -> None:
    route_window(gh, [])
    assert run_cli(["metrics", "report", *argv]) == 0
    assert query(gh) == f"repo:o/r is:pr is:merged merged:>={since}"
    assert capsys.readouterr().out.startswith(f"# Review metrics: {since} to 2026-03-03\n")


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5", "99999999"])
def test_a_bad_days_value_is_a_usage_error(gh, actions, capsys, value: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_cli(["metrics", "report", f"--days={value}"])
    assert exit_info.value.code == 2
    assert gh.calls == []
    assert "--days" in capsys.readouterr().err


def test_repo_option_overrides_the_environment(gh, actions) -> None:
    route_window(gh, [5])
    assert run_cli(["metrics", "report", "--repo", "acme/widgets", "--post-issue"]) == 0
    assert query(gh) == "repo:acme/widgets is:pr is:merged merged:>=2026-02-01"
    assert ("GET", "/repos/acme/widgets/pulls/5/commits") in calls(gh)
    assert calls(gh)[-1] == ("POST", "/repos/acme/widgets/issues")


@pytest.mark.parametrize("repo", ["", "widgets", "a/b/c"])
def test_a_missing_or_malformed_repository_is_an_error(gh, actions, capsys, monkeypatch, repo: str) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY")
    argv = ["metrics", "report"] + (["--repo", repo] if repo else [])
    assert run_cli(argv) == 1
    assert gh.calls == []
    assert capsys.readouterr().err.startswith("error: ")


def test_an_empty_window_still_produces_a_report(gh, actions, capsys) -> None:
    route_window(gh, [])
    assert run_cli(["metrics", "report", "--post-issue"]) == 0
    out = capsys.readouterr().out
    assert "| Revert rate | n/a |" in out and "| Mean rounds to pass | n/a |" in out
    assert calls(gh) == [("GET", "/search/issues"), ("POST", "/repos/o/r/labels"), ("POST", "/repos/o/r/issues")]


def test_a_missing_checklist_file_is_not_an_error(gh, actions, capsys) -> None:
    route_window(gh, [5])
    assert run_cli(["metrics", "report"]) == 0
    section = capsys.readouterr().out.split("## Prune candidates")[1]
    assert "n/a" in section and "- RC" not in section


def test_the_configured_checklist_and_agents_are_used(gh, actions, capsys) -> None:
    actions.config({"review": {"checklist_path": "ops/checks.md"}, "risk": {"agent_logins": ["alice"]}})
    actions.write("ops/checks.md", CHECKLIST)
    route_window(gh, [5])
    assert run_cli(["metrics", "report"]) == 0
    out = capsys.readouterr().out
    assert "\n- RC-02\n" in out
    assert "| Mean human approvals per pull request | 0.00 |" in out


def test_reaching_the_cap_is_said_in_the_report(gh, actions, capsys, monkeypatch) -> None:
    monkeypatch.setattr(metrics, "MAX_PULLS", 2)
    route_window(gh, [5, 9, 11])
    assert run_cli(["metrics", "report"]) == 0
    out = capsys.readouterr().out
    assert "| Merged pull requests | 2 |" in out
    assert "first 2 " in out.split("## ")[0]
    assert gh.calls_to("GET", r"/repos/o/r/pulls/11") == []


def test_below_the_cap_nothing_is_said_about_it(gh, actions, capsys) -> None:
    route_window(gh, [5])
    assert run_cli(["metrics", "report"]) == 0
    assert "first 200" not in capsys.readouterr().out


@pytest.mark.parametrize("pattern", [r"/search/issues", r"/repos/o/r/pulls/5/reviews"])
def test_a_github_failure_while_reading_is_exit_1(gh, actions, capsys, pattern: str) -> None:
    route_window(gh, [5])
    gh.on("GET", pattern, raises(503))
    assert run_cli(["metrics", "report", "--post-issue"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ") and len(captured.err.strip().splitlines()) == 1
    assert captured.out == ""
    assert [call for call in calls(gh) if call[0] == "POST"] == []


def test_a_failure_while_posting_is_exit_1_after_the_report_was_printed(gh, actions, capsys) -> None:
    route_window(gh, [5])
    gh.on("POST", r"/repos/o/r/issues", raises(410))
    assert run_cli(["metrics", "report", "--post-issue"]) == 1
    captured = capsys.readouterr()
    assert captured.out.startswith(f"# {TITLE}") and captured.err.startswith("error: ")


def test_a_broken_configuration_is_exit_1(gh, actions, capsys) -> None:
    actions.config({"risk": {"approvals": {"high": -1}}})
    assert run_cli(["metrics", "report"]) == 1
    assert gh.calls == [] and capsys.readouterr().err.startswith("error: ")


def test_an_oversized_report_is_cut_to_fit_an_issue(gh, actions, capsys, monkeypatch) -> None:
    route_window(gh, [5])
    monkeypatch.setattr(cmd_gates, "render_report", lambda *args, **kwargs: "# big\n" + "x" * 100_000)
    assert run_cli(["metrics", "report", "--post-issue"]) == 0
    body: Dict[str, Any] = gh.calls[-1]["body"]
    assert len(body["body"]) <= 65_000 and "cut" in body["body"][-200:]
