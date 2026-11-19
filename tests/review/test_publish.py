"""Publishing a review: one COMMENT review, thread upkeep, labels, a neutral check run."""

from __future__ import annotations

import json

import pytest

from reviewkit.config import parse_config
from reviewkit.github.client import GitHubError
from reviewkit.review.findings import Finding, Outcome
from reviewkit.review.plan import Plan
from reviewkit.review.publish import publish
from reviewkit.review.state import PriorFinding, latest_state

HEAD = "abc1234def5678" + "0" * 26
BOT = {"login": "github-actions[bot]", "type": "Bot"}


def cfg(**review):
    return parse_config({"review": review})[0]


def plan(**overrides):
    base = {
        "run": True,
        "reason": "run",
        "pr_number": 7,
        "head_sha": HEAD,
        "base_ref": "main",
        "merge_base": "m" * 40,
        "mode": "full",
        "round": 1,
        "level": "standard",
        "lenses": ("correctness",),
        "publish": "advisory",
        "patch": "p1",
    }
    base.update(overrides)
    return Plan(**base)


def finding(identifier="ab12cd34ef", severity="important", inline=True, line=12):
    return Finding(
        id=identifier,
        path="src/a.py",
        line=line,
        severity=severity,
        title=f"Issue {identifier}",
        body="Body.",
        evidence="Evidence.",
        quote="x = 1",
        lens="correctness",
        inline=inline,
        comment_path="src/a.py" if inline else None,
        comment_line=line if inline else None,
    )


def prior(identifier, severity="important", resolved=False, comment_id=501):
    return PriorFinding(
        id=identifier,
        thread_id=f"T-{identifier}",
        comment_id=comment_id,
        path="src/a.py",
        line=3,
        severity=severity,
        round=1,
        checklist_id="",
        resolved=resolved,
        outdated=False,
        title=f"Old {identifier}",
        excerpt="e",
    )


@pytest.fixture
def gh(github):
    github.on("POST", "/repos/o/r/pulls/7/reviews", {"id": 900})
    github.on("POST", "/repos/o/r/check-runs", {"id": 800})
    github.on("POST", "/repos/o/r/labels", {})
    github.on("POST", "/repos/o/r/issues/7/labels", [])
    github.on("DELETE", "/repos/o/r/issues/7/labels/.+", None)
    github.on("POST", "/repos/o/r/pulls/7/comments/\\d+/replies", {"id": 1})
    github.on("POST", "/repos/o/r/issues/7/comments", {"id": 700})
    github.on("PATCH", "/repos/o/r/issues/comments/\\d+", {"id": 700})
    github.on("GET", "/repos/o/r/issues/7/comments", [])
    github.graphql_handler = lambda query, variables: {"resolveReviewThread": {"thread": {"isResolved": True}}}
    return github


def run(gh, outcome=None, the_plan=None, config=None, threads=(), labels=(), review_outcome="success", **kw):
    return publish(
        gh,
        "o/r",
        the_plan or plan(),
        config or cfg(),
        outcome,
        list(threads),
        tuple(labels),
        review_outcome=review_outcome,
        run_url="https://example.invalid/run/1",
        now="2026-11-19T10:00:00Z",
        **kw,
    )


def writes(gh):
    return [(c["method"], c["path"]) for c in gh.calls if c["method"] != "GET"]


def test_advisory_review_posts_one_comment_review_with_inline_findings(gh):
    outcome = Outcome(summary="Two issues.", posted=[finding(), finding("n100000000", "nit", inline=False, line=40)])
    result = run(gh, outcome)
    [review] = gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")
    body = review["body"]
    assert body["event"] == "COMMENT" and body["commit_id"] == HEAD
    assert body["comments"] == [{"path": "src/a.py", "line": 12, "side": "RIGHT", "body": body["comments"][0]["body"]}]
    assert "reviewkit:finding" in body["comments"][0]["body"]
    assert "`src/a.py:40`" in body["body"]  # the summary-only finding is listed in the review body
    state = latest_state([{"body": body["body"], "user": BOT}], [], "github-actions")
    assert (state.round, state.head, state.patch, state.open_important) == (1, HEAD, "p1", ("ab12cd34ef",))
    assert result.exit_code == 0 and result.open_important == 1


def test_a_review_never_approves_or_requests_changes(gh):
    run(gh, Outcome(posted=[finding()]))
    run(gh, Outcome())
    events = {call["body"]["event"] for call in gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")}
    assert events == {"COMMENT"}


def test_check_run_is_neutral_named_from_config_and_ends_with_severity_counts(gh):
    run(gh, Outcome(posted=[finding(), finding("n100000000", "nit")]), config=cfg(check_name="My Review"))
    [check] = gh.calls_to("POST", "/repos/o/r/check-runs")
    body = check["body"]
    assert (body["name"], body["head_sha"], body["status"], body["conclusion"]) == (
        "My Review",
        HEAD,
        "completed",
        "neutral",
    )
    assert body["output"]["title"] == "Code review found 2 issues"
    assert (
        body["output"]["text"].rstrip().split("\n")[-1]
        == '<!-- bughunter-severity: {"nit":1,"normal":1,"pre_existing":0} -->'
    )


def test_round_label_replaces_the_previous_one(gh):
    run(gh, Outcome(), the_plan=plan(round=2), labels=("review-round:1", "bug"))
    assert ("DELETE", "/repos/o/r/issues/7/labels/review-round%3A1") in writes(gh)
    added = [c["body"]["labels"] for c in gh.calls_to("POST", "/repos/o/r/issues/7/labels")]
    assert ["review-round:2"] in added
    assert not any("bug" in path for _method, path in writes(gh))


def test_fixed_and_withdrawn_threads_get_a_reply_and_are_resolved(gh):
    outcome = Outcome()
    outcome.prior = {
        "aaaaaaaaaa": {"status": "fixed", "note": "A guard was added."},
        "bbbbbbbbbb": {"status": "withdrawn", "note": ""},
        "cccccccccc": {"status": "still_open", "note": ""},
    }
    threads = [
        prior("aaaaaaaaaa", comment_id=501),
        prior("bbbbbbbbbb", comment_id=502),
        prior("cccccccccc", comment_id=503),
    ]
    result = run(gh, outcome, threads=threads, the_plan=plan(round=2))
    replies = gh.calls_to("POST", "/repos/o/r/pulls/7/comments/\\d+/replies")
    assert sorted(c["path"].split("/")[-2] for c in replies) == ["501", "502"]
    assert "A guard was added." in replies[0]["body"]["body"]
    resolved = [c["params"]["id"] for c in gh.calls if c["method"] == "GRAPHQL"]
    assert sorted(resolved) == ["T-aaaaaaaaaa", "T-bbbbbbbbbb"]
    assert result.open_important == 1  # the still-open one keeps counting


def test_already_resolved_threads_are_left_alone(gh):
    outcome = Outcome()
    outcome.prior = {"aaaaaaaaaa": {"status": "fixed", "note": ""}}
    run(gh, outcome, threads=[prior("aaaaaaaaaa", resolved=True)])
    assert not [c for c in gh.calls if c["method"] == "GRAPHQL"]


def test_open_counts_include_earlier_findings_that_are_still_open(gh):
    threads = [prior("aaaaaaaaaa"), prior("bbbbbbbbbb", "nit"), prior("cccccccccc", resolved=True)]
    run(gh, Outcome(posted=[finding("n200000000", "nit")]), threads=threads, the_plan=plan(round=2))
    [check] = gh.calls_to("POST", "/repos/o/r/check-runs")
    assert check["body"]["output"]["text"].rstrip().split("\n")[-1] == (
        '<!-- bughunter-severity: {"nit":2,"normal":1,"pre_existing":0} -->'
    )


def test_three_rounds_with_important_findings_still_open_escalates_to_a_human(gh):
    result = run(gh, Outcome(posted=[finding()]), the_plan=plan(round=3))
    assert result.escalated
    added = [c["body"]["labels"] for c in gh.calls_to("POST", "/repos/o/r/issues/7/labels")]
    assert ["needs-human"] in added
    [review] = gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")
    assert "Needs a human" in review["body"]["body"]
    assert latest_state([{"body": review["body"]["body"], "user": BOT}], [], "github-actions").escalated


def test_no_escalation_before_the_limit_or_when_nothing_important_is_open(gh):
    assert not run(gh, Outcome(posted=[finding()]), the_plan=plan(round=2)).escalated
    assert not run(gh, Outcome(posted=[finding("n100000000", "nit")]), the_plan=plan(round=5)).escalated


def test_gate_mode_fails_the_job_only_when_important_findings_are_open(gh):
    gated = plan(publish="gate")
    assert run(gh, Outcome(posted=[finding()]), the_plan=gated).exit_code == 1
    assert run(gh, Outcome(posted=[finding("n100000000", "nit")]), the_plan=gated).exit_code == 0
    assert run(gh, Outcome(posted=[finding()])).exit_code == 0  # advisory never fails


def test_shadow_mode_keeps_findings_off_the_pull_request(gh):
    result = run(gh, Outcome(posted=[finding()]), the_plan=plan(publish="shadow"))
    assert not gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")
    [comment] = gh.calls_to("POST", "/repos/o/r/issues/7/comments")
    assert "shadow mode" in comment["body"]["body"] and "Issue ab12cd34ef" not in comment["body"]["body"]
    assert latest_state([], [{"body": comment["body"]["body"], "user": BOT}], "github-actions").round == 1
    assert gh.calls_to("POST", "/repos/o/r/check-runs")
    assert "Issue ab12cd34ef" in result.summary_markdown


def test_shadow_mode_updates_its_one_comment_instead_of_adding_more(gh):
    existing = {"id": 700, "user": BOT, "body": 'old <!-- reviewkit:state {"round":1} -->'}
    gh.on("GET", "/repos/o/r/issues/7/comments", [existing])
    run(gh, Outcome(), the_plan=plan(publish="shadow", round=2))
    assert gh.calls_to("PATCH", "/repos/o/r/issues/comments/700")
    assert not gh.calls_to("POST", "/repos/o/r/issues/7/comments")


def test_dry_run_writes_nothing_to_github(gh):
    result = run(gh, Outcome(posted=[finding()]), the_plan=plan(publish="dry_run"))
    assert writes(gh) == []
    assert "Issue ab12cd34ef" in result.summary_markdown


@pytest.mark.parametrize("review_outcome", ["failure", "cancelled", "skipped"])
def test_a_review_that_did_not_complete_is_reported_as_no_verdict(gh, review_outcome):
    result = run(gh, None, review_outcome=review_outcome)
    assert not gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")
    [check] = gh.calls_to("POST", "/repos/o/r/check-runs")
    assert check["body"]["output"]["title"] == "Code review did not complete"
    assert check["body"]["conclusion"] == "neutral"
    assert "bughunter-severity" not in check["body"]["output"]["text"]
    assert result.exit_code == 0
    assert not gh.calls_to("POST", "/repos/o/r/issues/7/labels")  # the round did not happen


def test_invalid_output_counts_as_not_completed_and_gate_mode_fails_closed(gh):
    invalid = Outcome(valid=False, error="no findings list")
    assert run(gh, invalid).exit_code == 0
    assert run(gh, invalid, the_plan=plan(publish="gate")).exit_code == 1
    assert not gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")


def test_skip_plans_post_only_what_they_ask_for(gh):
    quiet = Plan(run=False, reason="draft", pr_number=7, head_sha=HEAD)
    run(gh, None, the_plan=quiet)
    assert writes(gh) == []
    kept = Plan(
        run=False,
        reason="unchanged",
        pr_number=7,
        head_sha=HEAD,
        check_title="Code review skipped — no new changes",
        check_summary="Same lines.",
    )
    run(gh, None, the_plan=kept)
    [check] = gh.calls_to("POST", "/repos/o/r/check-runs")
    assert check["body"]["output"]["title"] == "Code review skipped — no new changes"
    assert "bughunter-severity" in check["body"]["output"]["text"]


def test_not_repeated_has_no_severity_line(gh):
    kept = Plan(
        run=False,
        reason="unchanged_open_findings",
        pr_number=7,
        head_sha=HEAD,
        check_title="Code review not repeated — earlier findings still open",
        check_summary="Open.",
    )
    run(gh, None, the_plan=kept)
    [check] = gh.calls_to("POST", "/repos/o/r/check-runs")
    assert "bughunter-severity" not in check["body"]["output"]["text"]


def test_unsubscribe_plan_removes_the_label_and_replies(gh):
    stop = Plan(
        run=False,
        reason="unsubscribed",
        pr_number=7,
        head_sha=HEAD,
        label_remove=("claude-review:always",),
        reply="Push-triggered reviews are off.",
    )
    run(gh, None, the_plan=stop, labels=("claude-review:always",))
    assert ("DELETE", "/repos/o/r/issues/7/labels/claude-review%3Aalways") in writes(gh)
    [comment] = gh.calls_to("POST", "/repos/o/r/issues/7/comments")
    assert comment["body"]["body"] == "Push-triggered reviews are off."


def test_subscribe_adds_the_label(gh):
    run(gh, Outcome(), the_plan=plan(label_add=("claude-review:always",)))
    added = [c["body"]["labels"] for c in gh.calls_to("POST", "/repos/o/r/issues/7/labels")]
    assert ["claude-review:always"] in added


def test_when_github_rejects_inline_positions_the_findings_move_to_the_summary(gh):
    attempts = []

    def reviews(match, params, body):
        attempts.append(body)
        if body.get("comments"):
            raise GitHubError(
                422, "POST", "/repos/o/r/pulls/7/reviews", "pull_request_review_thread.line must be part of the diff"
            )
        return {"id": 901}

    gh.on("POST", "/repos/o/r/pulls/7/reviews", reviews)
    result = run(gh, Outcome(posted=[finding()]))
    assert len(attempts) == 2 and "comments" not in attempts[1]
    assert "`src/a.py:12`" in attempts[1]["body"]
    assert result.exit_code == 0
    [check] = gh.calls_to("POST", "/repos/o/r/check-runs")
    assert check["body"]["output"]["annotations"][0]["path"] == "src/a.py"


def test_label_and_check_failures_do_not_lose_the_review(gh):
    def boom(match, params, body):
        raise GitHubError(403, "POST", match.group(0), "Resource not accessible by integration")

    gh.on("POST", "/repos/o/r/check-runs", boom)
    gh.on("POST", "/repos/o/r/issues/7/labels", boom)
    result = run(gh, Outcome(posted=[finding()]))
    assert gh.calls_to("POST", "/repos/o/r/pulls/7/reviews")
    assert result.exit_code == 0 and len(result.warnings) == 2


def test_summary_markdown_lists_dropped_and_rejected_candidates_for_tuning(gh):
    outcome = Outcome(posted=[finding()])
    outcome.dropped.append(
        {"title": "Ghost bug", "path": "x.py", "line": 1, "reason": "quoted code not found in the file"}
    )
    outcome.rejected.append(
        {"path": "y.py", "line": 2, "title": "Not a bug", "reason": "guarded two lines up", "lens": "correctness"}
    )
    text = run(gh, outcome).summary_markdown
    assert "Ghost bug" in text and "guarded two lines up" in text
    assert json.dumps(text)  # plain text, serializable


def test_labels_are_created_with_the_style_every_workflow_shares(gh):
    from reviewkit import labels as shared

    run(gh, Outcome(posted=[finding()]), the_plan=plan(round=3))
    created = {call["body"]["name"]: call["body"] for call in gh.calls_to("POST", "/repos/o/r/labels")}
    spec = shared.KIT_LABELS["needs-human"]
    assert (created["needs-human"]["color"], created["needs-human"]["description"]) == (spec.color, spec.description)
    assert created["review-round:3"]["color"] == "ededed"
