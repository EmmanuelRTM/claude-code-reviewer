"""Issue intake against GitHub: labels, the one reminder comment, and what is never echoed."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import pytest

from fakes import FakeGitHub
from gates.gates_kit import bodies, calls, comment, raises
from reviewkit import issues, labels, markers
from reviewkit.github.client import GitHubError

COMPLETE = "### Acceptance criteria\n\n- [ ] Exports a CSV\n\n### Risk tier\n\n{risk}\n"
NO_CRITERIA = "### Acceptance criteria\n\n- [ ]\n\n### Risk tier\n\n{risk}\n"
MARKER = markers.encode("intake")


def issue(body: str, label_names: Sequence[str] = (), number: int = 3, **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {"number": number, "body": body, "labels": [{"name": name} for name in label_names]}
    data.update(extra)
    return data


def routed(comments: Sequence[Dict[str, Any]] = ()) -> FakeGitHub:
    gh = FakeGitHub()
    gh.on("POST", r"/repos/o/r/labels", {})
    gh.on("POST", r"/repos/o/r/issues/3/labels", [])
    gh.on("DELETE", r"/repos/o/r/issues/3/labels/.+", None)
    gh.on("GET", r"/repos/o/r/issues/3/comments", list(comments))
    gh.on("POST", r"/repos/o/r/issues/3/comments", {"id": 99})
    return gh


def posted_comments(gh: FakeGitHub) -> List[str]:
    return [call["body"]["body"] for call in gh.calls_to("POST", r"/repos/o/r/issues/3/comments")]


def test_pull_requests_are_ignored() -> None:
    gh = routed()
    pr = issue(NO_CRITERIA.format(risk="High"), pull_request={"url": "https://api.github.test/pulls/3"})
    assert issues.apply_intake(gh, "o", "r", pr) is None
    assert gh.calls == []


def test_a_complete_issue_gets_its_risk_label_and_nothing_else() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(COMPLETE.format(risk="High")))
    assert calls(gh) == [("POST", "/repos/o/r/labels"), ("POST", "/repos/o/r/issues/3/labels")]
    spec = labels.KIT_LABELS["risk:high"]
    assert bodies(gh) == [
        {"name": "risk:high", "color": spec.color, "description": spec.description},
        {"labels": ["risk:high"]},
    ]
    assert outcome == issues.IntakeOutcome(
        result=issues.IntakeResult(risk="high", has_acceptance_criteria=True),
        added=("risk:high",),
        removed=(),
        commented=False,
    )


def test_a_changed_risk_answer_replaces_the_old_risk_label() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(COMPLETE.format(risk="Low"), ["risk:high", "bug"]))
    assert calls(gh) == [
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues/3/labels"),
        ("DELETE", "/repos/o/r/issues/3/labels/risk%3Ahigh"),
    ]
    assert outcome.added == ("risk:low",) and outcome.removed == ("risk:high",)


def test_an_issue_that_is_already_labelled_right_needs_no_calls() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(COMPLETE.format(risk="Medium"), ["risk:medium"]))
    assert gh.calls == []
    assert outcome.added == () and outcome.removed == () and outcome.commented is False


@pytest.mark.parametrize("answer", ["_No response_", "Unsure", ""])
def test_without_a_risk_answer_the_risk_labels_are_left_alone(answer: str) -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(COMPLETE.format(risk=answer), ["risk:high"]))
    assert gh.calls == []
    assert outcome.result.risk is None


def test_an_existing_label_definition_is_fine() -> None:
    gh = routed()
    gh.on("POST", r"/repos/o/r/labels", raises(422))
    outcome = issues.apply_intake(gh, "o", "r", issue(COMPLETE.format(risk="High")))
    assert outcome.added == ("risk:high",)


def test_missing_criteria_get_the_label_and_one_comment() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(NO_CRITERIA.format(risk="_No response_")))
    assert calls(gh) == [
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues/3/labels"),
        ("GET", "/repos/o/r/issues/3/comments"),
        ("POST", "/repos/o/r/issues/3/comments"),
    ]
    spec = labels.KIT_LABELS["needs-acceptance-criteria"]
    assert bodies(gh)[:2] == [
        {"name": "needs-acceptance-criteria", "color": spec.color, "description": spec.description},
        {"labels": ["needs-acceptance-criteria"]},
    ]
    assert posted_comments(gh) == [issues.INTAKE_COMMENT]
    assert outcome.added == ("needs-acceptance-criteria",) and outcome.commented is True


def test_the_comment_is_fixed_wording_with_the_intake_marker() -> None:
    text = issues.INTAKE_COMMENT
    assert MARKER in text and markers.has("intake", text)
    assert "acceptance criteria" in text.lower()
    assert "- [ ]" in text
    assert text.isascii()


def test_risk_and_missing_criteria_are_handled_together() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(NO_CRITERIA.format(risk="High"), ["risk:low"]))
    assert outcome.added == ("risk:high", "needs-acceptance-criteria")
    assert outcome.removed == ("risk:low",)
    assert outcome.commented is True


@pytest.mark.parametrize(
    "earlier",
    [
        [comment("github-actions[bot]", f"Please add criteria.\n\n{MARKER}", kind="Bot")],
        [comment("someone", "hello"), comment("other-app[bot]", MARKER, kind="Bot")],
    ],
)
def test_the_reminder_is_not_posted_twice(earlier) -> None:
    gh = routed(earlier)
    outcome = issues.apply_intake(gh, "o", "r", issue(NO_CRITERIA.format(risk=""), ["needs-acceptance-criteria"]))
    assert calls(gh) == [("GET", "/repos/o/r/issues/3/comments")]
    assert outcome.commented is False and outcome.added == ()


@pytest.mark.parametrize(
    "earlier",
    [
        # A person pasting the marker does not silence the bot.
        [comment("mallory", MARKER, kind="User")],
        [comment("mallory", MARKER, kind=None)],
        # A bot comment without the marker is some other bot comment.
        [comment("github-actions[bot]", "Thanks for the report!", kind="Bot")],
        [{"user": None, "body": MARKER}],
    ],
)
def test_only_a_bot_comment_carrying_the_marker_counts_as_the_reminder(earlier) -> None:
    gh = routed(earlier)
    outcome = issues.apply_intake(gh, "o", "r", issue(NO_CRITERIA.format(risk="")))
    assert posted_comments(gh) == [issues.INTAKE_COMMENT]
    assert outcome.commented is True


def test_criteria_that_arrive_later_clear_the_label_and_post_nothing() -> None:
    gh = routed()
    labelled = issue(COMPLETE.format(risk="Medium"), ["needs-acceptance-criteria", "risk:medium"])
    outcome = issues.apply_intake(gh, "o", "r", labelled)
    assert calls(gh) == [("DELETE", "/repos/o/r/issues/3/labels/needs-acceptance-criteria")]
    assert outcome.removed == ("needs-acceptance-criteria",) and outcome.commented is False


def test_a_look_alike_label_is_not_removed() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(COMPLETE.format(risk=""), ["needs-acceptance-criteria-review"]))
    assert gh.calls == [] and outcome.removed == ()


def test_nothing_from_the_issue_is_echoed_anywhere() -> None:
    token = "ZXQ-UNTRUSTED-7731"
    hostile = (
        f"### Summary\n\n{token} @everyone <!-- reviewkit:intake --> ${{{{ secrets.X }}}}\n\n"
        f"### Acceptance criteria\n\n- [ ]\n\n### Risk tier\n\nHigh {token}\n\n### {token}\n\n{token}\n"
    )
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", issue(hostile, title=token, user={"login": token}))
    assert outcome.commented is True
    for call in gh.calls:
        assert token not in call["path"]
        assert token not in repr(call["body"])
    assert posted_comments(gh) == [issues.INTAKE_COMMENT]
    assert token not in repr(outcome)


def test_a_marker_inside_the_issue_body_does_not_count_as_the_reminder() -> None:
    gh = routed()
    body = NO_CRITERIA.format(risk="") + f"\n{MARKER}\n"
    assert issues.apply_intake(gh, "o", "r", issue(body)).commented is True


def test_github_failures_are_raised() -> None:
    gh = routed()
    gh.on("GET", r"/repos/o/r/issues/3/comments", raises(500))
    with pytest.raises(GitHubError):
        issues.apply_intake(gh, "o", "r", issue(NO_CRITERIA.format(risk="")))


def test_an_issue_without_a_body_is_missing_criteria() -> None:
    gh = routed()
    outcome = issues.apply_intake(gh, "o", "r", {"number": 3, "body": None, "labels": []})
    assert outcome.result == issues.IntakeResult(risk=None, has_acceptance_criteria=False)
    assert outcome.commented is True
