"""`rk risk evaluate` end to end against the fake GitHub."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import pytest

from gates.gates_kit import HEAD, OLD, SHORT, Actions, bodies, calls, comment, pull, raises, review, run_cli, user
from reviewkit import cmd_gates

RUN_URL = "https://github.com/o/r/actions/runs/42"
STATUSES = r"/repos/o/r/statuses/[0-9a-f]{40}"


@pytest.fixture
def actions(tmp_path, monkeypatch) -> Actions:
    """A fake workflow step: environment, event file, output files and a project directory as cwd."""
    return Actions(tmp_path, monkeypatch)


@pytest.fixture
def gh(actions, github):
    """The in-memory GitHub, handed to the gates commands in place of a real client."""
    return actions.use_github(github)


def scenario(
    gh: Any,
    pr: Optional[Dict[str, Any]] = None,
    files: Sequence[Any] = ("src/app.py",),
    reviews: Sequence[Dict[str, Any]] = (),
    comments: Sequence[Dict[str, Any]] = (),
    permissions: Optional[Dict[str, str]] = None,
    siblings: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Route everything `risk evaluate` may ask about one pull request."""
    pr = pr or pull()
    number, head = pr["number"], pr["head"]["sha"]
    table = permissions or {}
    gh.on("GET", rf"/repos/o/r/pulls/{number}", pr)
    gh.on("GET", rf"/repos/o/r/pulls/{number}/files", [f if isinstance(f, dict) else {"filename": f} for f in files])
    gh.on("POST", r"/repos/o/r/labels", {})
    gh.on("POST", rf"/repos/o/r/issues/{number}/labels", [])
    gh.on("DELETE", rf"/repos/o/r/issues/{number}/labels/.+", None)
    gh.on("GET", rf"/repos/o/r/pulls/{number}/reviews", list(reviews))
    gh.on("GET", rf"/repos/o/r/issues/{number}/comments", list(comments))
    gh.on(
        "GET",
        r"/repos/o/r/collaborators/([^/]+)/permission",
        lambda match, params, body: {"permission": table.get(match.group(1), "write")},
    )
    gh.on("GET", rf"/repos/o/r/commits/{head}/pulls", list(siblings) if siblings is not None else [pr])
    gh.on("POST", rf"/repos/o/r/statuses/{head}", {"id": 1})
    return pr


def pr_event(pr: Dict[str, Any], action: str = "synchronize") -> Dict[str, Any]:
    return {"action": action, "number": pr["number"], "pull_request": pr}


def comment_event(body: Any, number: int = 7, on_pull: bool = True, login: str = "dev") -> Dict[str, Any]:
    issue: Dict[str, Any] = {"number": number, "title": "Add the export button"}
    if on_pull:
        issue["pull_request"] = {"url": f"https://api.github.test/repos/o/r/pulls/{number}"}
    return {"action": "created", "issue": issue, "comment": {"body": body, "user": user(login)}}


def status(gh: Any) -> Dict[str, Any]:
    (call,) = gh.calls_to("POST", STATUSES)
    return call["body"]


def test_medium_pull_request_with_one_approval_passes(gh, actions, capsys) -> None:
    pr = scenario(gh, reviews=[review("alice")])
    actions.event("pull_request_target", pr_event(pr, "opened"))
    assert run_cli(["risk", "evaluate"]) == 0
    assert calls(gh) == [
        ("GET", "/repos/o/r/pulls/7"),
        ("GET", "/repos/o/r/pulls/7/files"),
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues/7/labels"),
        ("GET", "/repos/o/r/pulls/7/reviews"),
        ("GET", "/repos/o/r/issues/7/comments"),
        ("GET", "/repos/o/r/collaborators/alice/permission"),
        ("GET", f"/repos/o/r/commits/{HEAD}/pulls"),
        ("POST", f"/repos/o/r/statuses/{HEAD}"),
    ]
    assert bodies(gh)[1] == {"labels": ["risk:medium"]}
    assert status(gh) == {
        "state": "success",
        "context": "risk-approval",
        "description": f"risk:medium: 1 of 1 human approvals on {SHORT}",
        "target_url": RUN_URL,
    }
    assert actions.outputs() == {"tier": "medium", "required": "1", "approvals": "1", "state": "success"}
    summary = actions.summary()
    assert "| #7 | medium | 1 | 1 | success |" in summary
    assert f"risk:medium: 1 of 1 human approvals on {SHORT}" in summary and "`alice`" in summary
    # Nothing is left to do, so the summary does not explain how to approve.
    assert "/approve" not in summary
    assert "success" in capsys.readouterr().out


def test_high_risk_paths_need_two_approvals(gh, actions) -> None:
    pr = scenario(gh, files=["README.md", ".github/workflows/ci.yml"], reviews=[review("alice")])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh) == {
        "state": "pending",
        "context": "risk-approval",
        "description": f"risk:high: 1 of 2 human approvals on {SHORT}",
        "target_url": RUN_URL,
    }
    assert {"labels": ["risk:high"]} in bodies(gh)
    assert actions.outputs() == {"tier": "high", "required": "2", "approvals": "1", "state": "pending"}
    # The summary tells people how to approve this exact commit, by its full sha, and who already did.
    summary = actions.summary()
    assert f"\n/approve {HEAD}\n" in summary and "/recheck" in summary
    assert "`alice`" in summary and "edited" in summary


def test_docs_only_pull_request_needs_nobody(gh, actions) -> None:
    pr = scenario(gh, files=["README.md", "docs/guide.md"])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh)["state"] == "success"
    assert status(gh)["description"] == f"risk:low: no human approval required for {SHORT}"
    assert actions.outputs() == {"tier": "low", "required": "0", "approvals": "0", "state": "success"}


def test_a_stale_risk_label_is_replaced(gh, actions) -> None:
    pr = scenario(gh, pr=pull(labels=["risk:low", "bug"]), files=["src/app.py"])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert calls(gh)[2:5] == [
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues/7/labels"),
        ("DELETE", "/repos/o/r/issues/7/labels/risk%3Alow"),
    ]
    assert actions.outputs()["tier"] == "medium"


def test_a_person_can_raise_the_tier_with_a_label_and_it_is_never_lowered(gh, actions) -> None:
    pr = scenario(gh, pr=pull(labels=["risk:high"]), files=["README.md"], reviews=[review("alice")])
    actions.event("pull_request_target", pr_event(pr, "labeled"))
    assert run_cli(["risk", "evaluate"]) == 0
    # The label is already right: no label calls at all, and certainly no removal.
    assert gh.calls_to("POST", r"/repos/o/r/labels") == []
    assert gh.calls_to("POST", r"/repos/o/r/issues/7/labels") == []
    assert gh.calls_to("DELETE", r".*") == []
    assert status(gh)["description"] == f"risk:high: 1 of 2 human approvals on {SHORT}"
    assert actions.outputs()["tier"] == "high"


def test_a_lower_label_does_not_pull_the_tier_below_the_paths(gh, actions) -> None:
    pr = scenario(gh, pr=pull(labels=["risk:low"]), files=["src/billing/invoice.py"])
    actions.event("pull_request_target", pr_event(pr, "labeled"))
    assert run_cli(["risk", "evaluate"]) == 0
    assert actions.outputs()["tier"] == "high"
    assert ("DELETE", "/repos/o/r/issues/7/labels/risk%3Alow") in calls(gh)


def test_both_names_of_a_renamed_file_count(gh, actions) -> None:
    renamed = {"filename": "docs/login.md", "previous_filename": "src/auth/login.md", "status": "renamed"}
    pr = scenario(gh, files=[renamed])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert actions.outputs()["tier"] == "high"


def test_a_file_listing_github_cut_short_is_treated_as_high_risk(gh, actions) -> None:
    pr = scenario(gh, pr=pull(changed_files=3500), files=["README.md"])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert actions.outputs()["tier"] == "high"


def test_the_pull_request_is_read_fresh_not_taken_from_the_event(gh, actions) -> None:
    scenario(gh, pr=pull(labels=["risk:high"]), files=["README.md"])
    stale = pull(head=OLD, labels=[])
    actions.event("pull_request_target", pr_event(stale))
    assert run_cli(["risk", "evaluate"]) == 0
    assert calls(gh)[-1] == ("POST", f"/repos/o/r/statuses/{HEAD}")
    assert actions.outputs()["tier"] == "high"


def test_the_author_vouches_with_an_approve_comment(gh, actions) -> None:
    approve = comment("dev", f"/approve {HEAD}")
    scenario(gh, comments=[comment("dev", "First attempt."), approve])
    actions.event("issue_comment", comment_event(f"/approve {HEAD}"))
    assert run_cli(["risk", "evaluate"]) == 0
    assert ("GET", "/repos/o/r/collaborators/dev/permission") in calls(gh)
    assert status(gh)["state"] == "success"
    assert actions.outputs()["approvals"] == "1"


def test_the_author_cannot_approve_natively(gh, actions) -> None:
    scenario(gh, pr=pull(author="dev"), reviews=[review("dev")])
    actions.event("pull_request_target", pr_event(pull()))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh)["state"] == "pending"
    assert actions.outputs()["approvals"] == "0"


def test_recheck_picks_up_a_native_approval(gh, actions) -> None:
    scenario(gh, reviews=[review("alice")], comments=[comment("dev", "/recheck")])
    actions.event("issue_comment", comment_event("/recheck"))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh)["state"] == "success"


def test_the_approval_must_come_from_github_not_from_the_event(gh, actions) -> None:
    scenario(gh, comments=[])
    actions.event("issue_comment", comment_event(f"/approve {HEAD}", login="alice"))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh)["state"] == "pending"


def test_people_without_push_access_and_agents_do_not_count(gh, actions) -> None:
    scenario(
        gh,
        reviews=[review("visitor"), review("claude[bot]", kind="Bot"), review("copilot")],
        comments=[comment("github-actions[bot]", f"/approve {HEAD}", kind="Bot")],
        permissions={"visitor": "read"},
    )
    actions.event("pull_request_target", pr_event(pull()))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh)["state"] == "pending"
    assert [path for method, path in calls(gh) if "collaborators" in path] == [
        "/repos/o/r/collaborators/visitor/permission"
    ]


@pytest.mark.parametrize(
    "event",
    [
        comment_event("Looks good to me!"),
        comment_event("Thanks.\n/approve a1b2c3d"),
        comment_event(" /recheck"),
        comment_event(""),
        comment_event(None),
        # A command on a plain issue is not for this gate.
        comment_event(f"/approve {HEAD}", on_pull=False),
        comment_event("/recheck", on_pull=False),
        {"action": "created"},
    ],
)
def test_a_comment_that_is_not_a_command_on_a_pull_request_makes_no_api_call(gh, actions, capsys, event) -> None:
    actions.event("issue_comment", event)
    assert run_cli(["risk", "evaluate"]) == 0
    assert gh.calls == []
    assert actions.outputs() == {} and actions.summary() == ""
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1 and captured.err == ""


@pytest.mark.parametrize("name", ["push", "pull_request", "pull_request_review", "workflow_dispatch", ""])
def test_other_events_are_not_evaluated(gh, actions, capsys, name: str) -> None:
    actions.event(name, pr_event(pull()))
    assert run_cli(["risk", "evaluate"]) == 0
    assert gh.calls == []
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_only_pull_requests_into_the_default_branch_are_gated(gh, actions, capsys) -> None:
    pr = scenario(gh, pr=pull(base="release/1.x"))
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert calls(gh) == [("GET", "/repos/o/r/pulls/7")]
    out = capsys.readouterr().out
    assert out.startswith("::notice::") and "main" in out
    # A branch name is text someone chose; it is not repeated.
    assert "release/1.x" not in out
    assert actions.outputs() == {}


def test_the_default_branch_comes_from_the_configuration(gh, actions) -> None:
    actions.config({"default_branch": "trunk"})
    pr = scenario(gh, pr=pull(base="trunk"), reviews=[review("alice")])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh)["state"] == "success"


@pytest.mark.parametrize("state", ["closed", "merged", None])
def test_a_closed_pull_request_is_left_alone_silently(gh, actions, capsys, state) -> None:
    scenario(gh, pr=pull(state=state))
    actions.event("issue_comment", comment_event("/recheck"))
    assert run_cli(["risk", "evaluate"]) == 0
    assert calls(gh) == [("GET", "/repos/o/r/pulls/7")]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert actions.outputs() == {} and actions.summary() == ""


@pytest.mark.parametrize(
    ("reviews", "comments", "complete"),
    [(2, 2, True), (3, 0, False), (0, 3, False)],
)
def test_the_gate_does_not_decide_on_a_partial_listing(
    gh, actions, capsys, monkeypatch, reviews: int, comments: int, complete: bool
) -> None:
    """A later review can withdraw an approval, so evidence that was cut short is no evidence."""
    monkeypatch.setattr(cmd_gates, "LISTING_LIMIT", 2)
    pr = scenario(
        gh,
        reviews=[review("alice")] + [review(f"r{index}", "COMMENTED") for index in range(reviews - 1)],
        comments=[comment(f"c{index}", "hello") for index in range(comments)],
    )
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == (0 if complete else 1)
    assert len(gh.calls_to("POST", STATUSES)) == (1 if complete else 0)
    if not complete:
        err = capsys.readouterr().err
        assert err.startswith("error: ") and "refusing to decide" in err
        assert actions.outputs() == {}


def test_another_open_pull_request_on_the_same_commit_holds_the_status(gh, actions) -> None:
    pr = pull()
    scenario(gh, pr=pr, reviews=[review("alice")], siblings=[pr, pull(number=12)])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    body = status(gh)
    assert body["state"] == "pending"
    assert "another open pull request" in body["description"] and "#12" in body["description"]
    assert body["description"] in actions.summary()
    assert actions.outputs()["state"] == "pending" and actions.outputs()["approvals"] == "1"


def test_configured_context_approval_counts_and_agents_are_used(gh, actions) -> None:
    actions.config(
        {
            "risk": {
                "status_context": "human-approval",
                "approvals": {"medium": 2},
                "agent_logins": ["alice"],
                "high_paths": ["infra/**"],
            }
        }
    )
    pr = scenario(gh, files=[".github/workflows/ci.yml"], reviews=[review("alice"), review("bob")])
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert status(gh) == {
        "state": "pending",
        "context": "human-approval",
        "description": f"risk:medium: 1 of 2 human approvals on {SHORT}",
        "target_url": RUN_URL,
    }


def test_without_a_run_url_the_status_has_no_link(gh, actions, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_RUN_ID")
    pr = scenario(gh)
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 0
    assert "target_url" not in status(gh)


def test_untrusted_text_never_reaches_statuses_labels_outputs_or_the_summary(gh, actions, capsys) -> None:
    token = "ZXQ-UNTRUSTED-7731"
    pr = pull(title=f"{token} $(curl evil)", body=token)
    pr["head"]["ref"] = f"{token}-branch"
    scenario(
        gh,
        pr=pr,
        files=[f"src/{token}.py"],
        reviews=[{**review("alice"), "body": token}],
        comments=[comment("dev", f"/approve {HEAD}\n{token}"), comment("mallory", f"{token} ::set-output name=x::y")],
    )
    actions.event("issue_comment", comment_event(f"/approve {HEAD}\n{token}"))
    assert run_cli(["risk", "evaluate"]) == 0
    for body in bodies(gh):
        assert token not in repr(body)
    assert token not in repr(actions.outputs()) and token not in actions.summary()
    captured = capsys.readouterr()
    assert token not in captured.out and token not in captured.err


@pytest.mark.parametrize(
    ("method", "pattern"),
    [
        ("GET", r"/repos/o/r/pulls/7"),
        ("GET", r"/repos/o/r/pulls/7/files"),
        ("POST", r"/repos/o/r/issues/7/labels"),
        ("GET", r"/repos/o/r/pulls/7/reviews"),
        ("GET", r"/repos/o/r/issues/7/comments"),
        ("GET", r"/repos/o/r/collaborators/alice/permission"),
        ("GET", rf"/repos/o/r/commits/{HEAD}/pulls"),
        ("POST", rf"/repos/o/r/statuses/{HEAD}"),
    ],
)
def test_a_github_failure_is_one_line_on_stderr_and_exit_1(gh, actions, capsys, method: str, pattern: str) -> None:
    pr = scenario(gh, reviews=[review("alice")])
    gh.on(method, pattern, raises(500))
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1 and "500" in err
    # Nothing is reported as decided when the evaluation did not finish.
    assert actions.outputs() == {}
    if method != "POST" or "statuses" not in pattern:
        assert gh.calls_to("POST", STATUSES) == []


def test_a_broken_configuration_is_reported_before_any_api_call(gh, actions, capsys) -> None:
    actions.config("{ not json")
    actions.event("pull_request_target", pr_event(pull()))
    assert run_cli(["risk", "evaluate"]) == 1
    assert gh.calls == []
    err = capsys.readouterr().err
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1


@pytest.mark.parametrize("repository", [None, "", "not-a-repo", "a/b/c"])
def test_a_missing_or_malformed_repository_is_an_error(gh, actions, capsys, monkeypatch, repository) -> None:
    if repository is None:
        monkeypatch.delenv("GITHUB_REPOSITORY")
    else:
        monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    actions.event("pull_request_target", pr_event(pull()))
    assert run_cli(["risk", "evaluate"]) == 1
    assert gh.calls == []
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert ("GITHUB_REPOSITORY" in err) is (not repository)


@pytest.mark.parametrize(
    "event",
    [{"action": "opened"}, {"pull_request": {"number": "7"}}, {"pull_request": {"number": True}}, {}],
)
def test_a_pull_request_event_without_a_number_is_an_error(gh, actions, capsys, event) -> None:
    actions.event("pull_request_target", event)
    assert run_cli(["risk", "evaluate"]) == 1
    assert gh.calls == []
    assert capsys.readouterr().err.startswith("error: ")


def test_a_head_that_is_not_a_full_sha_is_refused(gh, actions, capsys) -> None:
    pr = scenario(gh, pr=pull(head="main"))
    actions.event("pull_request_target", pr_event(pr))
    assert run_cli(["risk", "evaluate"]) == 1
    assert calls(gh) == [("GET", "/repos/o/r/pulls/7")]
    assert capsys.readouterr().err.startswith("error: ")
