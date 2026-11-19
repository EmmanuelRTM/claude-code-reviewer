"""Collecting the merged pull requests of a window from GitHub."""

from __future__ import annotations

from datetime import date
from typing import Any, List, Tuple

import pytest

from fakes import FakeGitHub
from gates.gates_kit import calls, commit, finding, raises, review, user
from reviewkit import metrics
from reviewkit.github.client import GitHubError


class LimitRecorder(FakeGitHub):
    """A fake that also remembers the `limit` each listing was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.listings: List[Tuple[str, Any, int]] = []

    def get_all(self, path, params=None, items_key=None, limit=3000):
        self.listings.append((path, items_key, limit))
        return super().get_all(path, params, items_key, limit)


def routed(items: List[Any]) -> LimitRecorder:
    gh = LimitRecorder()
    gh.on("GET", r"/search/issues", {"total_count": len(items), "incomplete_results": False, "items": items})
    gh.on(
        "GET",
        r"/repos/o/r/pulls/(\d+)",
        lambda match, params, body: {
            "number": int(match.group(1)),
            "title": f"Title of {match.group(1)}",
            "body": "This reverts commit 1a2b3c4.",
            "created_at": "2026-02-10T08:00:00Z",
            "merged_at": "2026-02-11T08:00:00Z",
            "user": user("dev"),
        },
    )
    gh.on("GET", r"/repos/o/r/pulls/\d+/reviews", [review("alice")])
    gh.on("GET", r"/repos/o/r/pulls/\d+/comments", [finding("nit", "RC-01", {"+1": 1})])
    gh.on("GET", r"/repos/o/r/pulls/\d+/commits", [commit("x\n\nVerified-By: verify")])
    return gh


def item(number: int, *label_names: str) -> dict:
    return {"number": number, "title": "title in the search index", "labels": [{"name": n} for n in label_names]}


def test_window() -> None:
    assert metrics.window(30, date(2026, 3, 3)) == (date(2026, 2, 1), date(2026, 3, 3))
    assert metrics.window(1, date(2026, 1, 1)) == (date(2025, 12, 31), date(2026, 1, 1))
    assert metrics.DEFAULT_DAYS == 30 and metrics.MAX_PULLS == 200


def test_collect_searches_merged_pull_requests_then_reads_each_one() -> None:
    gh = routed([item(5, "review-round:2", "bug"), item(9)])
    pulls = metrics.collect_pulls(gh, "o", "r", date(2026, 2, 1))
    assert calls(gh) == [
        ("GET", "/search/issues"),
        ("GET", "/repos/o/r/pulls/5"),
        ("GET", "/repos/o/r/pulls/5/reviews"),
        ("GET", "/repos/o/r/pulls/5/comments"),
        ("GET", "/repos/o/r/pulls/5/commits"),
        ("GET", "/repos/o/r/pulls/9"),
        ("GET", "/repos/o/r/pulls/9/reviews"),
        ("GET", "/repos/o/r/pulls/9/comments"),
        ("GET", "/repos/o/r/pulls/9/commits"),
    ]
    assert gh.calls[0]["params"] == {
        "q": "repo:o/r is:pr is:merged merged:>=2026-02-01",
        "sort": "created",
        "order": "desc",
    }
    assert gh.listings[0] == ("/search/issues", "items", 200)
    assert [pull["number"] for pull in pulls] == [5, 9]
    first = pulls[0]
    assert first["title"] == "Title of 5"
    assert first["body"] == "This reverts commit 1a2b3c4."
    assert first["created_at"] == "2026-02-10T08:00:00Z" and first["merged_at"] == "2026-02-11T08:00:00Z"
    assert first["user"] == user("dev")
    # Labels come from the search result, as plain names.
    assert first["labels"] == ["review-round:2", "bug"] and pulls[1]["labels"] == []
    assert first["reviews"] == [review("alice")]
    assert len(first["review_comments"]) == 1 and len(first["commits"]) == 1


def test_collected_records_feed_straight_into_the_computation() -> None:
    gh = routed([item(5, "review-round:1")])
    result = metrics.compute_metrics(metrics.collect_pulls(gh, "o", "r", date(2026, 2, 1)))
    assert result.reverts == metrics.RevertStats(1, 1, 1.0)
    assert result.rounds.mean == 1.0
    assert result.findings.by_checklist_id == {"RC-01": 1}
    assert result.trailers.verified == 1
    assert result.approvals.mean_per_pull == 1.0


def test_collect_stops_at_the_cap() -> None:
    gh = routed([item(1), item(2), item(3)])
    pulls = metrics.collect_pulls(gh, "o", "r", date(2026, 2, 1), limit=2)
    assert [pull["number"] for pull in pulls] == [1, 2]
    assert gh.listings[0] == ("/search/issues", "items", 2)
    assert gh.calls_to("GET", r"/repos/o/r/pulls/3") == []


def test_collect_skips_malformed_and_repeated_search_results() -> None:
    gh = routed([item(4), {"title": "no number"}, "junk", item(4), {"number": "7"}, item(6)])
    pulls = metrics.collect_pulls(gh, "o", "r", date(2026, 2, 1))
    assert [pull["number"] for pull in pulls] == [4, 6]


def test_an_empty_window_reads_nothing_else() -> None:
    gh = routed([])
    assert metrics.collect_pulls(gh, "o", "r", date(2026, 2, 1)) == []
    assert calls(gh) == [("GET", "/search/issues")]


def test_collect_raises_github_failures() -> None:
    gh = routed([item(5)])
    gh.on("GET", r"/repos/o/r/pulls/\d+/commits", raises(502))
    with pytest.raises(GitHubError):
        metrics.collect_pulls(gh, "o", "r", date(2026, 2, 1))
