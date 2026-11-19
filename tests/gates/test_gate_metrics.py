"""Review metrics computed from plain pull request records (no GitHub involved)."""

from __future__ import annotations

import pytest

from gates.gates_kit import commit, finding, review, user
from gates.gates_kit import hours_after_creation as hours
from gates.gates_kit import merged_pull as record
from reviewkit import markers, metrics


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ('Revert "Add the export button"', "", True),
        ('revert "add the export button"', "", True),
        ("Revert: add the export button", "", True),
        ("REVERT: add the export button", "", True),
        ('  Revert "padded"', "", True),
        ("Fix the importer", "This reverts commit 1a2b3c4d.", True),
        ("Fix the importer", "Context first.\n\nThis reverts commit 1a2b3c4d.\n", True),
        ("Revert the default of the flag", "", False),
        ("Reverting to the old parser", "", False),
        ('Handle Revert "x" titles', "", False),
        ("Fix the importer", "this change reverts nothing", False),
        ("", "", False),
        (None, None, False),
    ],
)
def test_is_revert(title, body, expected: bool) -> None:
    assert metrics.is_revert(title, body) is expected


def test_revert_rate() -> None:
    pulls = [record(1), record(2, title='Revert "x"'), record(3, body="This reverts commit abc."), record(4)]
    assert metrics.revert_stats(pulls) == metrics.RevertStats(merged=4, reverts=2, rate=0.5)
    assert metrics.revert_stats([]) == metrics.RevertStats(merged=0, reverts=0, rate=None)
    assert metrics.revert_stats([record(1)]).rate == 0.0


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ([], None),
        (["bug", "risk:low"], None),
        (["review-round:1"], 1),
        (["review-round:2", "bug"], 2),
        (["review-round:1", "review-round:3", "review-round:2"], 3),
        (["review-round:12"], 12),
        (["Review-Round:4"], 4),
        (["review-round:x"], None),
        (["review-round:"], None),
        (["review-round:0"], None),
        (["review-round:-2"], None),
        (["review-round:2 "], 2),
        (["my-review-round:2"], None),
    ],
)
def test_rounds_from_labels(labels, expected) -> None:
    assert metrics.rounds_from_labels(labels) == expected


def test_round_stats() -> None:
    rounds = [1, 1, 2, 3, 4, 5, None, 7]
    pulls = [record(i, labels=[f"review-round:{n}"] if n else ["bug"]) for i, n in enumerate(rounds)]
    stats = metrics.round_stats(pulls)
    assert stats.reviewed == 7 and stats.not_reviewed == 1
    assert stats.mean == pytest.approx(23 / 7)
    assert stats.one_round_share == pytest.approx(2 / 7)
    assert stats.five_plus_share == pytest.approx(2 / 7)


def test_round_stats_without_reviewed_pull_requests() -> None:
    assert metrics.round_stats([record(1), record(2)]) == metrics.RoundStats(0, 2, None, None, None)
    assert metrics.round_stats([]) == metrics.RoundStats(0, 0, None, None, None)


@pytest.mark.parametrize(
    ("reactions", "expected"),
    [
        ({}, "unrated"),
        (None, "unrated"),
        ({"+1": 1}, "useful"),
        ({"+1": 3, "heart": 1}, "useful"),
        # First match wins: -1, then confused, then eyes, then +1.
        ({"-1": 1, "+1": 5}, "wrong"),
        ({"-1": 1, "confused": 1, "eyes": 1, "+1": 1}, "wrong"),
        ({"confused": 1, "eyes": 1, "+1": 1}, "not_worth_it"),
        ({"eyes": 2, "+1": 1}, "intentional"),
        ({"-1": 0, "confused": 0, "eyes": 0, "+1": 0}, "unrated"),
        ({"heart": 2, "rocket": 1, "laugh": 1, "hooray": 1, "total_count": 5}, "unrated"),
        ({"-1": "1", "+1": None}, "unrated"),
        ({"+1": True}, "unrated"),
        ("+1", "unrated"),
    ],
)
def test_finding_outcome(reactions, expected: str) -> None:
    assert metrics.finding_outcome(reactions) == expected


def test_finding_stats_counts_bot_comments_that_carry_a_finding_marker() -> None:
    comments = [
        finding("important", "RC-01", {"+1": 1}),
        finding("important", "RC-01", {"+1": 2}),
        finding("important", None, {"-1": 1}),
        finding("nit", "RC-02", {"confused": 1}),
        finding("nit", None, {"eyes": 1}),
        finding("pre_existing", "RC-01", {}),
        finding("blocker", None, {}),
        # Not findings: a person pasting a marker, and a bot comment without one.
        finding("important", "RC-09", {"+1": 9}, kind="User"),
        {"user": user("github-actions[bot]", "Bot"), "body": "Summary of the review.", "reactions": {"+1": 1}},
        {"user": None, "body": markers.encode("finding", {"sev": "nit"}), "reactions": {}},
    ]
    stats = metrics.finding_stats([record(1, review_comments=comments[:4]), record(2, review_comments=comments[4:])])
    assert stats.total == 7
    assert stats.by_outcome == {"useful": 2, "wrong": 1, "not_worth_it": 1, "intentional": 1, "unrated": 2}
    assert stats.by_severity == {"important": 3, "nit": 2, "pre_existing": 1, "unknown": 1}
    assert stats.cells["important"] == {"useful": 2, "wrong": 1, "not_worth_it": 0, "intentional": 0, "unrated": 0}
    assert stats.cells["nit"]["not_worth_it"] == 1 and stats.cells["nit"]["intentional"] == 1
    assert stats.cells["unknown"]["unrated"] == 1
    # useful / (useful + wrong + not worth it); "intentional" and "unrated" are left out.
    assert stats.precision == pytest.approx(2 / 4)
    assert stats.by_checklist_id == {"RC-01": 3, "RC-02": 1}
    assert stats.uncited == 3


def test_precision_is_unknown_until_something_is_rated() -> None:
    assert metrics.finding_stats([]).precision is None
    unrated = metrics.finding_stats([record(1, review_comments=[finding(), finding(reactions={"eyes": 1})])])
    assert unrated.total == 2 and unrated.precision is None
    assert metrics.finding_stats([record(1, review_comments=[finding(reactions={"-1": 1})])]).precision == 0.0


def test_the_last_finding_marker_in_a_comment_is_the_one_that_counts() -> None:
    body = "\n".join([markers.encode("finding", {"sev": "nit"}), markers.encode("finding", {"sev": "important"})])
    stats = metrics.finding_stats(
        [record(1, review_comments=[{"user": user("b[bot]", "Bot"), "body": body, "reactions": {}}])]
    )
    assert stats.by_severity["important"] == 1 and stats.by_severity["nit"] == 0


@pytest.mark.parametrize("cid", ["<script>alert(1)</script>", "RC-1", "rc-01", "RC-" + "9" * 40, "", 7, ["RC-01"]])
def test_only_well_formed_checklist_ids_become_report_rows(cid) -> None:
    stats = metrics.finding_stats([record(1, review_comments=[finding(cid=cid)])])
    assert stats.by_checklist_id == {}
    assert stats.uncited == 1 and stats.total == 1


@pytest.mark.parametrize(
    ("message", "status", "reason"),
    [
        ("Add export\n\nVerified-By: verify, code-review", "verified", ""),
        ("Add export\n\nVerified-By: verify, code-review\n", "verified", ""),
        ("Add export\r\n\r\nVerified-By: verify\r\n", "verified", ""),
        ("Fix typo\n\nVerified-By: none (docs only)", "opted_out", "docs only"),
        ("Fix typo\n\nVerified-By: None", "opted_out", ""),
        ("Fix typo\n\nVerified-By: NONE (generated file) and more", "opted_out", "generated file"),
        ("Fix typo\n\nVerified-By: none: docs", "opted_out", ""),
        ("Fix typo\n\nVerified-By: none (first) (second)", "opted_out", "first"),
        ("Fix typo\n\nVerified-By: none (never closed", "opted_out", ""),
        ("Fix typo\n\nVerified-By: none )(", "opted_out", ""),
        ("Fix typo\n\nVerified-By: none ()", "opted_out", ""),
        ("Fix typo\n\nVerified-By: none docs)", "opted_out", ""),
        ("Add export", "missing", ""),
        ("", "missing", ""),
        (None, "missing", ""),
        ("Add export\n\nVerified-By:", "missing", ""),
        ("Add export\n\nVerified-By:   ", "missing", ""),
        ("Add export\r\n\r\nVerified-By:\r\n", "missing", ""),
        ("Add export\r\n\r\nVerified-By: none (docs only)\r\n", "opted_out", "docs only"),
        # The last trailer line decides.
        ("x\n\nVerified-By: verify\nVerified-By: none (rebased)", "opted_out", "rebased"),
        ("x\n\nVerified-By: none (wip)\nVerified-By: verify", "verified", ""),
        ("x\n\nVerified-By: verify\nVerified-By:", "missing", ""),
        # Trailer keys are case-insensitive; other text is not a trailer.
        ("x\n\nverified-by: verify", "verified", ""),
        ("x\n\nSee Verified-By: verify above", "missing", ""),
        ("x\n\n  Verified-By: verify", "missing", ""),
        ("x\n\nNot-Verified-By: verify", "missing", ""),
        # "none" is a word, not a prefix of another tool's name.
        ("x\n\nVerified-By: nonesuch-runner", "verified", ""),
        ("x\n\nVerified-By: verify\nCo-Authored-By: Someone <s@example.invalid>", "verified", ""),
    ],
)
def test_classify_trailer(message, status: str, reason: str) -> None:
    assert metrics.classify_trailer(message) == metrics.TrailerVerdict(status, reason)


def test_trailer_stats_cover_every_commit_of_every_pull_request() -> None:
    pulls = [
        record(1, commits=[commit("a\n\nVerified-By: verify"), commit("b"), commit("c\n\nVerified-By: none (docs)")]),
        record(2, commits=[commit("d\n\nVerified-By: verify, code-review"), {"sha": "x"}, {"commit": None}]),
        record(3),
    ]
    assert metrics.trailer_stats(pulls) == metrics.TrailerStats(commits=6, verified=2, opted_out=1, missing=3)
    assert metrics.trailer_stats([]) == metrics.TrailerStats(0, 0, 0, 0)


def test_approval_stats_count_people_whose_latest_review_approves() -> None:
    pulls = [
        record(
            1,
            reviews=[
                review("alice", at=hours(2)),
                review("alice", "COMMENTED", at=hours(6)),
                review("bob", at=hours(1)),
                review("bob", "CHANGES_REQUESTED", at=hours(3)),
                review("carol", "CHANGES_REQUESTED", at=hours(4)),
                review("carol", at=hours(5)),
                review("review-app[bot]", at=hours(1), kind="Bot"),
                review("claude", at=hours(1)),
                {**review("ghost", at=hours(1)), "user": None},
            ],
        ),
        record(2, reviews=[review("alice", at=hours(4)), review("alice", at=hours(9))]),
        record(3, reviews=[review("dave", "COMMENTED", at=hours(1))]),
        record(4),
    ]
    stats = metrics.approval_stats(pulls, ("claude",))
    assert stats.pulls == 4 and stats.approved_pulls == 2
    # Pull request 1: alice and carol. Pull request 2: alice. The others: nobody.
    assert stats.mean_per_pull == pytest.approx(3 / 4)
    # First approvals after 2 hours and after 4 hours.
    assert stats.median_hours_to_first == pytest.approx(3.0)


def test_without_an_agent_list_only_bot_accounts_are_left_out() -> None:
    stats = metrics.approval_stats([record(1, reviews=[review("claude", at=hours(1))])], ())
    assert stats.mean_per_pull == 1.0


def test_a_dismissed_approval_is_not_an_approval() -> None:
    stats = metrics.approval_stats([record(1, reviews=[review("alice", "DISMISSED", at=hours(1))])], ())
    assert stats.mean_per_pull == 0.0 and stats.median_hours_to_first is None
    # A dismissed review that came after a standing approval is that person's latest verdict.
    reviews = [review("alice", at=hours(1)), review("alice", "DISMISSED", at=hours(2))]
    assert metrics.approval_stats([record(1, reviews=reviews)], ()).mean_per_pull == 0.0


def test_one_person_is_counted_once_however_the_login_is_cased() -> None:
    withdrawn = [review("Alice", at=hours(1)), review("alice", "CHANGES_REQUESTED", at=hours(2))]
    assert metrics.approval_stats([record(1, reviews=withdrawn)], ()).mean_per_pull == 0.0
    twice = [review("Alice", at=hours(1)), review("alice", at=hours(2))]
    assert metrics.approval_stats([record(1, reviews=twice)], ()).mean_per_pull == 1.0


def test_the_first_approval_is_the_earliest_one_by_someone_who_still_approves() -> None:
    reviews = [
        review("bob", at=hours(1)),
        review("bob", "CHANGES_REQUESTED", at=hours(2)),
        review("alice", at=hours(7)),
    ]
    stats = metrics.approval_stats([record(1, reviews=reviews)], ())
    assert stats.median_hours_to_first == pytest.approx(7.0)


def test_median_of_an_odd_number_of_pull_requests() -> None:
    pulls = [record(i, reviews=[review("alice", at=hours(h))]) for i, h in enumerate([1, 10, 2.5])]
    assert metrics.approval_stats(pulls, ()).median_hours_to_first == pytest.approx(2.5)


def test_approval_stats_when_nothing_can_be_computed() -> None:
    assert metrics.approval_stats([], ()) == metrics.ApprovalStats(0, 0, None, None)
    assert metrics.approval_stats([record(1)], ()) == metrics.ApprovalStats(1, 0, 0.0, None)
    unreadable = record(1, reviews=[review("alice", at="not a time")], created_at="also not a time")
    stats = metrics.approval_stats([unreadable], ())
    assert stats.mean_per_pull == 1.0 and stats.median_hours_to_first is None


def test_compute_metrics_on_an_empty_window() -> None:
    result = metrics.compute_metrics([])
    assert result.reverts.rate is None
    assert result.rounds.mean is None and result.rounds.one_round_share is None
    assert result.findings.total == 0 and result.findings.precision is None
    assert result.findings.by_outcome == dict.fromkeys(metrics.OUTCOMES, 0)
    assert result.trailers.commits == 0
    assert result.approvals.mean_per_pull is None and result.approvals.median_hours_to_first is None


def test_compute_metrics_puts_the_sections_together() -> None:
    pulls = [
        record(
            1,
            labels=["review-round:1"],
            reviews=[review("alice", at=hours(2)), review("claude", at=hours(1))],
            review_comments=[finding("nit", "RC-01", {"+1": 1})],
            commits=[commit("a\n\nVerified-By: verify")],
        ),
        record(2, title='Revert "Add the export button"'),
    ]
    result = metrics.compute_metrics(pulls, agent_logins=("claude",))
    assert result.reverts == metrics.RevertStats(2, 1, 0.5)
    assert result.rounds == metrics.RoundStats(1, 1, 1.0, 1.0, 0.0)
    assert result.findings.precision == 1.0 and result.findings.by_checklist_id == {"RC-01": 1}
    assert result.trailers == metrics.TrailerStats(1, 1, 0, 0)
    assert result.approvals == metrics.ApprovalStats(2, 1, 0.5, 2.0)
