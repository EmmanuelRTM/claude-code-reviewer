"""The kit's labels: the table, creating them, and keeping one of a group on an issue."""

from __future__ import annotations

import re

import pytest

from fakes import FakeGitHub
from gates.gates_kit import calls, raises
from reviewkit import labels
from reviewkit.github.client import GitHubError

ISSUE_LABELS = r"/repos/o/r/issues/7/labels"


def test_the_table_holds_the_documented_names_and_colors() -> None:
    assert {name: spec.color for name, spec in labels.KIT_LABELS.items()} == {
        "risk:low": "0e8a16",
        "risk:medium": "fbca04",
        "risk:high": "d93f0b",
        "needs-human": "b60205",
        "needs-acceptance-criteria": "c5def5",
        "review-metrics": "5319e7",
        "claude-review:always": "1d76db",
    }


@pytest.mark.parametrize("name", sorted(labels.KIT_LABELS))
def test_every_label_fits_what_github_accepts(name: str) -> None:
    spec = labels.KIT_LABELS[name]
    assert re.fullmatch(r"[0-9a-f]{6}", spec.color)
    assert 0 < len(spec.description) <= 100 and "\n" not in spec.description
    assert len(name) <= 50


def test_label_names_reads_rest_objects_and_plain_strings() -> None:
    assert labels.label_names([{"name": "bug"}, "risk:low", {"id": 1}, {"name": ""}, None]) == ["bug", "risk:low"]
    assert labels.label_names(None) == []


def test_ensure_label_creates_it(github: FakeGitHub) -> None:
    github.on("POST", r"/repos/o/r/labels", {"name": "risk:low"})
    assert labels.ensure_label(github, "o", "r", "risk:low", "0e8a16", "Low risk") is True
    assert github.calls == [
        {
            "method": "POST",
            "path": "/repos/o/r/labels",
            "params": {},
            "body": {"name": "risk:low", "color": "0e8a16", "description": "Low risk"},
        }
    ]


def test_ensure_label_treats_already_exists_as_success(github: FakeGitHub) -> None:
    github.on("POST", r"/repos/o/r/labels", raises(422))
    assert labels.ensure_label(github, "o", "r", "risk:low", "0e8a16", "Low risk") is False


def test_ensure_label_lets_other_errors_through(github: FakeGitHub) -> None:
    github.on("POST", r"/repos/o/r/labels", raises(403))
    with pytest.raises(GitHubError) as err:
        labels.ensure_label(github, "o", "r", "risk:low", "0e8a16", "Low risk")
    assert err.value.status == 403


def test_ensure_kit_label_takes_color_and_description_from_the_table(github: FakeGitHub) -> None:
    github.on("POST", r"/repos/o/r/labels", {})
    labels.ensure_kit_label(github, "o", "r", "review-metrics")
    spec = labels.KIT_LABELS["review-metrics"]
    assert github.calls[0]["body"] == {
        "name": "review-metrics",
        "color": spec.color,
        "description": spec.description,
    }
    with pytest.raises(KeyError):
        labels.ensure_kit_label(github, "o", "r", "not-a-kit-label")


def routed() -> FakeGitHub:
    gh = FakeGitHub()
    gh.on("POST", ISSUE_LABELS, [])
    gh.on("DELETE", ISSUE_LABELS + r"/.+", None)
    return gh


@pytest.mark.parametrize(
    ("current", "wanted", "expected_calls", "expected_result"),
    [
        # The wanted label replaces the other one of its group.
        (
            ["risk:low"],
            "risk:high",
            [("POST", "/repos/o/r/issues/7/labels"), ("DELETE", "/repos/o/r/issues/7/labels/risk%3Alow")],
            (["risk:high"], ["risk:low"]),
        ),
        # Already exactly right: nothing to do.
        (["risk:high", "bug"], "risk:high", [], ([], [])),
        # Present next to a stale one: only the stale one goes.
        (
            ["risk:low", "bug", "risk:medium"],
            "risk:medium",
            [("DELETE", "/repos/o/r/issues/7/labels/risk%3Alow")],
            ([], ["risk:low"]),
        ),
        # No label of the group yet: add only.
        (["bug"], "risk:low", [("POST", "/repos/o/r/issues/7/labels")], (["risk:low"], [])),
        # `wanted=None` clears the group and leaves other labels alone.
        (
            ["risk:low", "bug", "risk:high"],
            None,
            [
                ("DELETE", "/repos/o/r/issues/7/labels/risk%3Alow"),
                ("DELETE", "/repos/o/r/issues/7/labels/risk%3Ahigh"),
            ],
            ([], ["risk:low", "risk:high"]),
        ),
        (["bug"], None, [], ([], [])),
        # GitHub compares label names without regard to case.
        (["Risk:High"], "risk:high", [], ([], [])),
        (
            ["RISK:Low"],
            "risk:high",
            [("POST", "/repos/o/r/issues/7/labels"), ("DELETE", "/repos/o/r/issues/7/labels/RISK%3ALow")],
            (["risk:high"], ["RISK:Low"]),
        ),
    ],
)
def test_set_exclusive_label(current, wanted, expected_calls, expected_result) -> None:
    gh = routed()
    result = labels.set_exclusive_label(gh, "o", "r", 7, "risk:", wanted, current)
    assert calls(gh) == expected_calls
    assert result == expected_result
    for call in gh.calls_to("POST", ISSUE_LABELS):
        assert call["body"] == {"labels": [wanted]}


def test_set_exclusive_label_accepts_rest_label_objects() -> None:
    gh = routed()
    current = [{"name": "risk:low"}, {"name": "bug"}]
    assert labels.set_exclusive_label(gh, "o", "r", 7, "risk:", "risk:low", current) == ([], [])
    assert gh.calls == []


def test_a_label_that_is_already_gone_is_not_an_error() -> None:
    gh = FakeGitHub()
    gh.on("DELETE", ISSUE_LABELS + r"/.+", raises(404))
    assert labels.set_exclusive_label(gh, "o", "r", 7, "risk:", None, ["risk:low"]) == ([], [])
    assert calls(gh) == [("DELETE", "/repos/o/r/issues/7/labels/risk%3Alow")]


def test_other_failures_while_removing_are_raised() -> None:
    gh = FakeGitHub()
    gh.on("DELETE", ISSUE_LABELS + r"/.+", raises(500))
    with pytest.raises(GitHubError):
        labels.set_exclusive_label(gh, "o", "r", 7, "risk:", None, ["risk:low"])


def test_label_names_are_url_encoded_in_the_path() -> None:
    gh = routed()
    labels.set_exclusive_label(gh, "o", "r", 7, "risk:", None, ["risk:needs review/now?"])
    assert calls(gh) == [("DELETE", "/repos/o/r/issues/7/labels/risk%3Aneeds%20review%2Fnow%3F")]


def test_add_and_remove_single_labels() -> None:
    gh = routed()
    labels.add_label(gh, "o", "r", 7, "needs-human")
    assert labels.remove_label(gh, "o", "r", 7, "needs-human") is True
    assert calls(gh) == [
        ("POST", "/repos/o/r/issues/7/labels"),
        ("DELETE", "/repos/o/r/issues/7/labels/needs-human"),
    ]
    assert gh.calls[0]["body"] == {"labels": ["needs-human"]}
    gone = FakeGitHub()
    gone.on("DELETE", ISSUE_LABELS + r"/.+", raises(404))
    assert labels.remove_label(gone, "o", "r", 7, "needs-human") is False
