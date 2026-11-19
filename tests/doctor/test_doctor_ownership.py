"""CODEOWNERS coverage: close enough to GitHub's matching to tell 'owned' from 'open'."""

from __future__ import annotations

import pytest

from reviewkit.doctor.ownership import owners_of, pattern_matches, uncovered


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("*", ".claude/settings.json", True),
        ("/.claude/", ".claude/settings.json", True),
        ("/.claude/", "src/.claude/settings.json", False),
        (".claude/", "src/.claude/settings.json", True),
        ("/.github/workflows/", ".github/workflows/claude-review.yml", True),
        ("/.github/workflows/", ".github/dependabot.yml", False),
        (".github/", ".github/workflows/claude-review.yml", True),
        ("*.md", "REVIEW.md", True),
        ("*.md", "docs/reliability-checklist.md", True),
        ("*.js", "REVIEW.md", False),
        ("/REVIEW.md", "REVIEW.md", True),
        ("/REVIEW.md", "docs/REVIEW.md", False),
        ("REVIEW.md", "docs/REVIEW.md", True),
        ("docs/*", "docs/reliability-checklist.md", True),
        ("docs/*", "docs/sub/file.md", False),
        ("/docs/**", "docs/sub/file.md", True),
        ("**/workflows/*.yml", ".github/workflows/claude-review.yml", True),
        ("/", "REVIEW.md", True),
    ],
)
def test_pattern_matching(pattern, path, expected):
    assert pattern_matches(pattern, path) is expected


def test_the_last_matching_pattern_wins_and_can_remove_owners():
    text = "* @everyone\n/.claude/ @octo/maintainers  # the review rules\n/.claude/state/\n"
    assert owners_of(text, "src/app.py") == ["@everyone"]
    assert owners_of(text, ".claude/settings.json") == ["@octo/maintainers"]
    assert owners_of(text, ".claude/state/gate.log") == []


def test_comments_and_blank_lines_are_ignored():
    assert owners_of("# /REVIEW.md @someone\n\n", "REVIEW.md") == []


def test_uncovered_reports_labels_without_an_owner():
    text = "/.claude/ @octo/maintainers\n"
    samples = {".claude/": ".claude/settings.json", "REVIEW.md": "REVIEW.md"}
    assert uncovered(text, samples) == ["REVIEW.md"]
