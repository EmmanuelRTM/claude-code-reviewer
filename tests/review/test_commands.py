"""`@claude review` comment commands, following the managed service's rules."""

from __future__ import annotations

import pytest

from reviewkit.review.commands import parse_review_command


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("@claude review", "review"),
        ("@claude review\n", "review"),
        ("@Claude Review", "review"),
        ("@claude review once", "review"),
        ("@claude review always", "always"),
        ("@claude review ALWAYS please", "always"),
        ("@claude review stop", "stop"),
        ("@claude review off", "stop"),
        ("@claude review pause", "stop"),
        ("@claude review   always", "always"),
        # Other text may follow the command on the same line or below it.
        ("@claude review this one carefully\nthanks", "review"),
        # A misspelled or later-line keyword is a plain single review.
        ("@claude review alwys", "review"),
        ("@claude review\nalways", "review"),
    ],
)
def test_commands(body, expected):
    assert parse_review_command(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "",
        None,
        "please @claude review",  # not at the start of the comment
        "  @claude review",  # leading text, even whitespace, is not the start
        "@claude reviewed this already",  # a different word
        "@claude reviewer",
        "@claude fix the bug",
        "@claude-bot review",
        "@claudereview",
        "> @claude review",  # quoted reply
        "review",
    ],
)
def test_not_commands(body):
    assert parse_review_command(body) is None
