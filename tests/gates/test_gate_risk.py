"""Risk tiers: path rules, the tier of a pull request, and what labels may change."""

from __future__ import annotations

import itertools

import pytest

from reviewkit import risk
from reviewkit.config import Approvals, RiskConfig

CFG = RiskConfig()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # High-risk paths.
        (".github/workflows/ci.yml", "high"),
        (".claude/settings.json", "high"),
        ("db/migrations/0001_init.sql", "high"),
        ("services/api/migrations/0002.sql", "high"),
        ("src/auth/login.py", "high"),
        ("src/billing/invoice.py", "high"),
        # High wins over low when a path matches both lists.
        ("REVIEW.md", "high"),
        ("services/api/REVIEW.md", "high"),
        ("CLAUDE.md", "high"),
        ("docs/reliability-checklist.md", "high"),
        ("src/auth/README.md", "high"),
        # Low-risk paths.
        ("docs/guide/intro.md", "low"),
        ("docs/diagram.png", "low"),
        ("README.md", "low"),
        ("notes/todo.txt", "low"),
        ("LICENSE", "low"),
        # Everything else is medium.
        ("src/app.py", "medium"),
        ("src/authz/roles.py", "medium"),
        ("pkg/LICENSE", "medium"),
        ("Makefile", "medium"),
    ],
)
def test_file_tier(path: str, expected: str) -> None:
    assert risk.file_tier(path, CFG) == expected


def test_file_tier_follows_the_configured_patterns() -> None:
    cfg = RiskConfig(high_paths=("infra/**",), low_paths=("**/*.rst",))
    assert risk.file_tier("infra/main.tf", cfg) == "high"
    assert risk.file_tier("guide.rst", cfg) == "low"
    assert risk.file_tier(".github/workflows/ci.yml", cfg) == "medium"


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], "medium"),
        (["README.md"], "low"),
        (["README.md", "docs/a.md"], "low"),
        (["README.md", "src/app.py"], "medium"),
        (["src/app.py"], "medium"),
        (["README.md", "src/auth/login.py"], "high"),
        (["src/app.py", "README.md", ".github/workflows/ci.yml"], "high"),
    ],
)
def test_pr_tier_is_the_highest_tier_among_the_files(paths, expected: str) -> None:
    assert risk.pr_tier(paths, CFG) == expected


def test_required_approvals() -> None:
    assert [risk.required_approvals(tier, CFG) for tier in ("low", "medium", "high")] == [0, 1, 2]
    custom = RiskConfig(approvals=Approvals(low=1, medium=2, high=3))
    assert [risk.required_approvals(tier, custom) for tier in ("low", "medium", "high")] == [1, 2, 3]
    with pytest.raises(ValueError):
        risk.required_approvals("urgent", CFG)


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ([], None),
        (["bug", "enhancement"], None),
        (["risk:low"], "low"),
        (["risk:medium", "bug"], "medium"),
        (["risk:low", "risk:high"], "high"),
        (["risk:high", "risk:medium", "risk:low"], "high"),
        (["Risk:Medium"], "medium"),
        (["risk:urgent"], None),
        (["risk:"], None),
        (["at-risk:high"], None),
    ],
)
def test_tier_from_labels(labels, expected) -> None:
    assert risk.tier_from_labels(labels) == expected


def test_label_entries_that_are_not_names_are_skipped() -> None:
    assert risk.tier_from_labels([None, 7, {"name": "risk:high"}, "risk:low"]) == "low"
    assert risk.final_tier("medium", (None, "risk:high")) == "high"


def test_tier_label_round_trips() -> None:
    for tier in risk.TIERS:
        assert risk.tier_label(tier) == f"risk:{tier}"
        assert risk.tier_from_labels([risk.tier_label(tier)]) == tier


@pytest.mark.parametrize(
    ("computed", "labels", "expected"),
    [
        ("low", [], "low"),
        ("high", [], "high"),
        # A person raised the tier with a label.
        ("low", ["risk:high"], "high"),
        ("medium", ["risk:high", "bug"], "high"),
        # A label never pulls the tier below what the paths say.
        ("high", ["risk:low"], "high"),
        ("medium", ["risk:low"], "medium"),
        # Automation never lowers the tier below what a label says.
        ("low", ["risk:medium"], "medium"),
        ("low", ["risk:medium", "risk:high"], "high"),
        ("medium", ["risk:medium"], "medium"),
    ],
)
def test_final_tier(computed: str, labels, expected: str) -> None:
    assert risk.final_tier(computed, labels) == expected


def test_final_tier_is_never_below_either_input() -> None:
    rank = {tier: index for index, tier in enumerate(risk.TIERS)}
    label_sets = [[]] + [[f"risk:{tier}"] for tier in risk.TIERS] + [["risk:low", "risk:high"]]
    for computed, labels in itertools.product(risk.TIERS, label_sets):
        final = risk.final_tier(computed, labels)
        assert rank[final] >= rank[computed]
        labelled = risk.tier_from_labels(labels)
        if labelled is not None:
            assert rank[final] >= rank[labelled]
        assert final in (computed, labelled)


def test_changed_paths_counts_both_names_of_a_rename() -> None:
    files = [
        {"filename": "src/app.py", "status": "modified"},
        {"filename": "docs/login.md", "previous_filename": "src/auth/login.md", "status": "renamed"},
        {"status": "modified"},
        {"filename": ""},
    ]
    assert risk.changed_paths(files) == ["src/app.py", "docs/login.md", "src/auth/login.md"]


def test_moving_a_file_out_of_a_high_risk_directory_is_still_high() -> None:
    files = [{"filename": "docs/login.md", "previous_filename": "src/auth/login.md", "status": "renamed"}]
    assert risk.tier_for_pull(files, 1, CFG) == "high"


@pytest.mark.parametrize(
    ("filenames", "changed_files", "expected"),
    [
        (["README.md", "docs/a.md"], 2, "low"),
        (["README.md", "docs/a.md"], None, "low"),
        (["src/app.py"], 1, "medium"),
        ([], 0, "medium"),
        # GitHub lists at most 3000 files. A file we cannot see could be high-risk.
        (["README.md", "docs/a.md"], 3, "high"),
        ([], 5000, "high"),
        # A nonsensical count is not a reason to trust a listing more.
        (["README.md"], "many", "low"),
    ],
)
def test_tier_for_pull_fails_closed_when_the_listing_is_incomplete(filenames, changed_files, expected) -> None:
    files = [{"filename": name} for name in filenames]
    assert risk.tier_for_pull(files, changed_files, CFG) == expected


@pytest.mark.parametrize(("listed", "expected"), [(2999, "low"), (3000, "high"), (3001, "high")])
def test_a_listing_as_long_as_github_allows_is_assumed_to_be_cut_short(listed: int, expected: str) -> None:
    files = [{"filename": f"docs/page-{index}.md"} for index in range(listed)]
    assert risk.tier_for_pull(files, None, CFG) == expected
    assert risk.tier_for_pull(files, listed, CFG) == expected
