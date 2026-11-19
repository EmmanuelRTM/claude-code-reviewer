"""Glob matching used for risk tiers, protected paths and gate exemptions."""

from __future__ import annotations

import pytest

from reviewkit.paths import glob_match, match_any, normalize_path


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # A bare name is anchored at the repository root.
        ("REVIEW.md", "REVIEW.md", True),
        ("REVIEW.md", "docs/REVIEW.md", False),
        # `**/` matches zero or more directories.
        ("**/REVIEW.md", "REVIEW.md", True),
        ("**/REVIEW.md", "services/billing/REVIEW.md", True),
        ("**/REVIEW.md", "services/billing/REVIEW.md.bak", False),
        # `*` never crosses a directory separator.
        ("*.md", "README.md", True),
        ("*.md", "docs/guide.md", False),
        ("**/*.md", "docs/guide.md", True),
        ("src/*.py", "src/app.py", True),
        ("src/*.py", "src/pkg/app.py", False),
        # A trailing `/**` covers everything below a directory.
        (".github/**", ".github/workflows/ci.yml", True),
        (".github/**", ".github", False),
        (".github/**", ".githubx/file", False),
        # A trailing slash is shorthand for the same thing.
        ("docs/", "docs/a/b.txt", True),
        ("docs/", "docsx/a.txt", False),
        # `**` in the middle.
        ("src/**/migrations/**", "src/app/db/migrations/001.sql", True),
        ("src/**/migrations/**", "src/migrations/001.sql", True),
        ("**/auth/**", "services/auth/login.py", True),
        ("**/auth/**", "services/authz/login.py", False),
        # `?` matches one character that is not a separator.
        ("v?.txt", "v1.txt", True),
        ("v?.txt", "v/.txt", False),
        # Regex metacharacters in a pattern are literal.
        ("a+b(1).txt", "a+b(1).txt", True),
        ("a+b(1).txt", "aab(1).txt", False),
        ("**", "anything/at/all.txt", True),
    ],
)
def test_glob_match(pattern: str, path: str, expected: bool) -> None:
    assert glob_match(pattern, path) is expected


def test_match_any_returns_false_for_no_patterns() -> None:
    assert match_any((), "a.py") is False


def test_match_any_checks_every_pattern() -> None:
    assert match_any(("docs/**", "**/*.md"), "src/readme.md") is True
    assert match_any(("docs/**", "**/*.md"), "src/readme.py") is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("./src/app.py", "src/app.py"),
        ("src\\win\\app.py", "src/win/app.py"),
        ("src//app.py", "src/app.py"),
        ("/src/app.py", "src/app.py"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", ["../escape.py", "a/../../b.py", ""])
def test_normalize_path_rejects_escapes_and_empty(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_path(raw)
