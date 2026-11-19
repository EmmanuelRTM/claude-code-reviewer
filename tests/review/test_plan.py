"""Deciding whether and how to review: the deterministic part of every run."""

from __future__ import annotations

import pytest

from reviewkit.config import parse_config
from reviewkit.review import plan as planning
from reviewkit.review.plan import PullFacts, Trigger, finalize, parse_trigger, plan_lenses, precheck, pull_facts
from reviewkit.review.state import PriorFinding, ReviewState


def cfg(**review):
    return parse_config({"review": review})[0]


def pull(**overrides):
    base = {
        "number": 7,
        "open": True,
        "draft": False,
        "fork": False,
        "author": "alice",
        "author_type": "User",
        "base_ref": "main",
        "base_sha": "b" * 40,
        "head_sha": "h" * 40,
        "head_ref": "feature",
        "labels": (),
    }
    base.update(overrides)
    return PullFacts(**base)


AUTO = Trigger(kind="pull_request", action="synchronize", pr_number=7)
OPENED = Trigger(kind="pull_request", action="opened", pr_number=7)
COMMAND = Trigger(kind="command", command="review", pr_number=7, actor="bob", actor_type="User")
STATS = {"files": 3, "additions": 120, "deletions": 10, "diff_bytes": 9000}


def check(trigger=AUTO, facts=None, config=None, state=None, has_token=True, permission="write"):
    return precheck(trigger, facts or pull(), config or cfg(), state, has_token, permission)


def final(
    trigger=AUTO, facts=None, config=None, state=None, threads=(), patch="p1", stats=None, high_risk=False, tier=""
):
    return finalize(
        trigger, facts or pull(), config or cfg(), state, threads, "m" * 40, patch, stats or STATS, high_risk, tier
    )


def prior(identifier, resolved=False, severity="important"):
    return PriorFinding(
        id=identifier,
        thread_id="T",
        comment_id=1,
        path="a.py",
        line=1,
        severity=severity,
        round=1,
        checklist_id="",
        resolved=resolved,
        outdated=False,
        title="t",
        excerpt="e",
    )


# -- trigger parsing ---------------------------------------------------------


def test_parse_pull_request_event():
    event = {"action": "synchronize", "pull_request": {"number": 7}}
    assert parse_trigger("pull_request_target", event, {}) == AUTO


def test_parse_comment_command_and_ignore_everything_else():
    event = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {"url": "x"}},
        "comment": {"body": "@claude review always", "user": {"login": "bob", "type": "User"}},
    }
    trigger = parse_trigger("issue_comment", event, {})
    assert (trigger.kind, trigger.command, trigger.pr_number, trigger.actor) == ("command", "always", 7, "bob")
    plain_issue = dict(event, issue={"number": 7})
    assert parse_trigger("issue_comment", plain_issue, {}).kind == "ignored"
    chatter = dict(event, comment={"body": "nice work", "user": {"login": "bob", "type": "User"}})
    assert parse_trigger("issue_comment", chatter, {}).kind == "ignored"
    edited = dict(event, action="edited")
    assert parse_trigger("issue_comment", edited, {}).kind == "ignored"
    assert parse_trigger("push", {}, {}).kind == "ignored"


def test_parse_manual_dispatch_defaults_to_a_dry_run():
    trigger = parse_trigger("workflow_dispatch", {}, {"INPUT_PR_NUMBER": "12"})
    assert (trigger.kind, trigger.pr_number, trigger.dry_run) == ("dispatch", 12, True)
    live = parse_trigger("workflow_dispatch", {}, {"INPUT_PR_NUMBER": "12", "INPUT_DRY_RUN": "false"})
    assert live.dry_run is False
    assert parse_trigger("workflow_dispatch", {}, {"INPUT_PR_NUMBER": "abc"}).kind == "ignored"


def test_pull_facts_detects_forks_and_bots():
    pr = {
        "number": 7,
        "state": "open",
        "draft": True,
        "user": {"login": "dependabot[bot]", "type": "Bot"},
        "base": {"ref": "main", "sha": "b", "repo": {"full_name": "o/r"}},
        "head": {"ref": "x", "sha": "h", "repo": {"full_name": "fork/r"}},
        "labels": [{"name": "needs-human"}],
    }
    facts = pull_facts(pr)
    assert facts.fork and facts.draft and facts.author_type == "Bot" and facts.labels == ("needs-human",)
    pr["head"]["repo"] = None  # the fork was deleted
    assert pull_facts(pr).fork
    pr["head"]["repo"] = {"full_name": "o/r"}
    assert not pull_facts(pr).fork


# -- precheck: reasons not to start ------------------------------------------


def test_a_normal_push_continues():
    assert check() is None


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (pull(draft=True), "draft"),
        (pull(fork=True), "fork"),
        (pull(author_type="Bot"), "bot_author"),
        (pull(open=False), "closed"),
    ],
)
def test_automatic_reviews_skip_quietly(facts, reason):
    result = check(facts=facts)
    assert (result.run, result.reason, result.check_title) == (False, reason, "")


def test_a_person_can_request_a_review_of_a_draft_or_a_fork():
    assert check(trigger=COMMAND, facts=pull(draft=True)) is None
    assert check(trigger=COMMAND, facts=pull(fork=True)) is None
    assert check(trigger=COMMAND, facts=pull(open=False)).reason == "closed"


@pytest.mark.parametrize("permission", ["read", "triage", "none", ""])
def test_commands_need_write_access_and_never_come_from_bots(permission):
    assert check(trigger=COMMAND, permission=permission).reason == "not_permitted"
    bot = Trigger(kind="command", command="review", pr_number=7, actor="some-app[bot]", actor_type="Bot")
    assert check(trigger=bot, permission="write").reason == "not_permitted"


def test_ignored_events_do_nothing():
    assert check(trigger=Trigger(kind="ignored")).reason == "ignored"


def test_missing_token_is_reported_on_the_pull_request():
    result = check(has_token=False)
    assert result.reason == "missing_token" and "not configured" in result.check_title
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result.check_summary


@pytest.mark.parametrize(
    ("trigger_policy", "action", "labels", "runs"),
    [
        ("every_push", "synchronize", (), True),
        ("once", "opened", (), True),
        ("once", "ready_for_review", (), True),
        ("once", "synchronize", (), False),
        ("once", "synchronize", ("claude-review:always",), True),
        ("manual", "opened", (), False),
        ("manual", "synchronize", ("claude-review:always",), True),
    ],
)
def test_trigger_policy(trigger_policy, action, labels, runs):
    trigger = Trigger(kind="pull_request", action=action, pr_number=7)
    result = check(trigger=trigger, facts=pull(labels=labels), config=cfg(trigger=trigger_policy))
    assert (result is None) is runs
    if not runs:
        assert result.reason == "trigger_policy"


def test_stop_command_unsubscribes_and_replies():
    stop = Trigger(kind="command", command="stop", pr_number=7, actor="bob", actor_type="User")
    result = check(trigger=stop, config=cfg(trigger="manual"))
    assert result.reason == "unsubscribed" and result.label_remove == ("claude-review:always",)
    assert "@claude review" in result.reply
    every_push = check(trigger=stop)
    assert every_push.label_remove == () and "draft" in every_push.reply


def test_escalated_pull_requests_pause_until_a_person_acts():
    state = ReviewState(round=3, head="x", escalated=True)
    paused = check(facts=pull(labels=("needs-human",)), state=state)
    assert paused.reason == "paused" and "needs a human" in paused.check_title
    # Removing the label, or asking explicitly, resumes reviews.
    assert check(state=state) is None
    assert check(trigger=COMMAND, facts=pull(labels=("needs-human",)), state=state) is None
    keep_going = cfg(after_escalation="continue")
    assert check(facts=pull(labels=("needs-human",)), state=state, config=keep_going) is None


# -- finalize: how to review -------------------------------------------------


def test_first_review_is_a_full_round_one():
    result = final(trigger=OPENED)
    assert (result.run, result.mode, result.round, result.level) == (True, "full", 1, "standard")
    assert result.lenses == ("correctness", "contracts", "tests-evidence")
    assert result.publish == "advisory" and result.merge_base == "m" * 40 and result.patch == "p1"


def test_later_automatic_reviews_are_incremental():
    state = ReviewState(round=1, head="old" * 13 + "o", patch="p0")
    result = final(state=state)
    assert (result.mode, result.round, result.last_reviewed_sha) == ("incremental", 2, state.head)


def test_a_requested_review_always_covers_the_whole_pull_request():
    state = ReviewState(round=2, head="old", patch="p1")
    result = final(trigger=COMMAND, state=state)
    assert (result.run, result.mode, result.round, result.manual) == (True, "full", 3, True)


def test_same_lines_and_nothing_open_keeps_the_earlier_result():
    state = ReviewState(round=1, head="old", patch="p1")
    result = final(state=state)
    assert (result.run, result.reason) == (False, "unchanged")
    assert result.check_title == "Code review skipped — no new changes"


def test_same_lines_with_open_important_findings_is_not_repeated():
    state = ReviewState(round=1, head="old", patch="p1", open_important=("aaaaaaaaaa",))
    result = final(state=state, threads=[prior("aaaaaaaaaa")])
    assert (result.run, result.reason) == (False, "unchanged_open_findings")
    assert result.check_title == "Code review not repeated — earlier findings still open"


def test_resolving_an_important_thread_makes_the_next_push_reviewable():
    state = ReviewState(round=1, head="old", patch="p1", open_important=("aaaaaaaaaa",))
    assert final(state=state, threads=[prior("aaaaaaaaaa", resolved=True)]).run is True


def test_oversized_diffs_are_skipped_with_an_explanation():
    result = final(stats=dict(STATS, diff_bytes=10_000_000))
    assert (result.run, result.reason) == (False, "too_large")
    assert "too large" in result.check_title


def test_empty_diffs_are_skipped_quietly():
    result = final(stats={"files": 0, "additions": 0, "deletions": 0, "diff_bytes": 0})
    assert (result.run, result.reason, result.check_title) == (False, "empty_diff", "")


def test_dispatch_is_a_dry_run_unless_asked():
    dry = final(trigger=Trigger(kind="dispatch", pr_number=7, dry_run=True))
    assert dry.publish == "dry_run" and dry.manual
    live = final(trigger=Trigger(kind="dispatch", pr_number=7, dry_run=False), config=cfg(publish="gate"))
    assert live.publish == "gate"


def test_always_subscribes_except_on_forks():
    always = Trigger(kind="command", command="always", pr_number=7, actor="bob", actor_type="User")
    assert final(trigger=always).label_add == ("claude-review:always",)
    assert final(trigger=always, facts=pull(fork=True)).label_add == ()


def test_continue_policy_reviews_in_escalation_mode():
    state = ReviewState(round=4, head="old", patch="p0", escalated=True)
    result = final(state=state, config=cfg(after_escalation="continue"))
    assert result.mode == "escalation"


@pytest.mark.parametrize(
    ("level", "lines", "high_risk", "expected"),
    [
        ("quick", 500, False, ("all",)),
        ("quick", 500, True, ("all",)),
        ("standard", 10, False, ("all",)),
        ("standard", 10, True, ("correctness", "contracts", "tests-evidence", "security")),
        ("standard", 300, False, ("correctness", "contracts", "tests-evidence")),
        ("standard", 300, True, ("correctness", "contracts", "tests-evidence", "security")),
        ("deep", 10, False, ("correctness", "contracts", "tests-evidence", "security", "cross-file")),
    ],
)
def test_lens_plan(level, lines, high_risk, expected):
    assert plan_lenses(level, lines, high_risk) == expected


def test_plan_round_trips_through_json():
    result = final()
    again = planning.plan_from_json(planning.plan_to_json(result))
    assert again == result
    assert planning.plan_from_json("not json") is None
    assert planning.plan_from_json("") is None


def test_lenses_are_spread_over_more_than_one_model():
    from reviewkit.review.plan import lens_assignments

    assigned = lens_assignments(cfg(), ("correctness", "contracts", "tests-evidence", "security", "cross-file"))
    assert assigned == [
        {"id": "correctness", "model": "sonnet"},
        {"id": "contracts", "model": "sonnet"},
        {"id": "tests-evidence", "model": "haiku"},
        {"id": "security", "model": "opus"},
        {"id": "cross-file", "model": "sonnet"},
    ]
    custom = cfg(models={"finder": "opus", "finder_light": "sonnet"})
    assert lens_assignments(custom, ("all", "tests-evidence")) == [
        {"id": "all", "model": "opus"},
        {"id": "tests-evidence", "model": "sonnet"},
    ]


# -- skipping by risk tier (saves subscription usage on changes nobody needs reviewed) ----


def test_by_default_every_tier_is_reviewed():
    assert final(tier="low").run


def test_a_skipped_tier_gets_a_neutral_check_that_says_how_to_ask_for_a_review():
    plan = final(config=cfg(skip_risk_tiers=["low"]), tier="low")
    assert not plan.run and plan.reason == "risk_tier"
    assert plan.check_title == planning.TITLE_SKIPPED_TIER
    assert "low" in plan.check_summary and "@claude review" in plan.check_summary


def test_tiers_that_are_not_listed_are_still_reviewed():
    assert final(config=cfg(skip_risk_tiers=["low"]), tier="medium").run
    assert final(config=cfg(skip_risk_tiers=["low"]), tier="high").run


def test_a_person_can_always_ask_for_a_review_of_a_skipped_tier():
    assert final(trigger=COMMAND, config=cfg(skip_risk_tiers=["low"]), tier="low").run


def test_a_pull_request_subscribed_with_review_always_is_not_skipped():
    facts = pull(labels=(planning.SUBSCRIBED_LABEL,))
    assert final(facts=facts, config=cfg(skip_risk_tiers=["low"]), tier="low").run
