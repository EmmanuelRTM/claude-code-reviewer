"""The reliability checklist: reading its table and linting it."""

from __future__ import annotations

from typing import List, Tuple

import pytest

from reviewkit import checklist
from reviewkit.checklist import ChecklistItem, LintIssue

HEADER = "| ID | Trigger (readable from a diff) | Check | Origin |\n| --- | --- | --- | --- |\n"

DOCUMENT = """# Reliability checklist

Each row is one check the reviewer applies when the trigger shows up in a diff.

| ID | Trigger (readable from a diff) | Check | Origin |
| --- | --- | --- | --- |
| RC-01 | A new outbound HTTP call | It sets a timeout | Incident 2025-03 |
| RC-02 | A `a \\| b` union type in a query | Both branches are indexed | PR 412 |
|RC-103|No spaces around cells|Still a row|Review|

Rows below this line belong to another table.

| ID | Trigger | Check | Origin |
| --- | --- | --- | --- |
| RC-99 | Second table | Ignored | Nowhere |
"""


def table(*rows: str) -> str:
    return HEADER + "".join(row + "\n" for row in rows)


def lint(text: str) -> List[Tuple[str, int]]:
    return [(issue.level, issue.line) for issue in checklist.lint_checklist(text)]


def test_parse_reads_the_first_checklist_table() -> None:
    assert checklist.parse_checklist(DOCUMENT) == [
        ChecklistItem("RC-01", "A new outbound HTTP call", "It sets a timeout", "Incident 2025-03", 7),
        ChecklistItem("RC-02", "A `a | b` union type in a query", "Both branches are indexed", "PR 412", 8),
        ChecklistItem("RC-103", "No spaces around cells", "Still a row", "Review", 9),
    ]


def test_the_shipped_example_is_clean() -> None:
    assert checklist.lint_checklist(DOCUMENT) == []


@pytest.mark.parametrize(
    "header",
    [
        "| ID | Trigger (readable from a diff) | Check | Origin |",
        "| id | trigger | check | origin |",
        "|ID|Trigger|Check|Origin|",
        "| Id | Triggers | Checks | Origin (where it came from) |",
        "  | ID | Trigger | Check | Origin |  ",
    ],
)
def test_the_header_is_matched_by_how_its_cells_start(header: str) -> None:
    text = f"{header}\n|---|---|---|---|\n| RC-01 | t | c | o |\n"
    assert [item.id for item in checklist.parse_checklist(text)] == ["RC-01"]


@pytest.mark.parametrize("separator", ["| --- | --- | --- | --- |", "|:---|:---:|---:|-|", "| :-- | --- | --- | --: |"])
def test_separator_rows_with_alignment_are_skipped(separator: str) -> None:
    text = f"| ID | Trigger | Check | Origin |\n{separator}\n| RC-01 | t | c | o |\n"
    assert [item.id for item in checklist.parse_checklist(text)] == ["RC-01"]


def test_tables_with_another_header_are_passed_over() -> None:
    text = (
        "| Name | Owner |\n| --- | --- |\n| api | team-a |\n\n"
        "| Trigger | ID | Check | Origin |\n| --- | --- | --- | --- |\n| t | RC-50 | c | o |\n\n"
        + table("| RC-07 | t | c | o |")
    )
    items = checklist.parse_checklist(text)
    assert [(item.id, item.line) for item in items] == [("RC-07", 11)]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# Reliability checklist\n\nNothing here yet.\n",
        "| ID | Trigger | Check |\n| --- | --- | --- |\n| RC-01 | t | c |\n",
        "| ID | Trigger | Check | Origin | Owner |\n| --- | --- | --- | --- | --- |\n| RC-01 | t | c | o | me |\n",
        # A header that is not followed by a separator row is not a Markdown table.
        "| ID | Trigger | Check | Origin |\n| RC-01 | t | c | o |\n",
        "| ID | Trigger | Check | Origin |\n\n| --- | --- | --- | --- |\n| RC-01 | t | c | o |\n",
        "| ID | Trigger | Check | Origin |\n| --- | --- | --- |\n| RC-01 | t | c | o |\n",
        "| ID | Trigger | Check | Origin |",
    ],
)
def test_no_checklist_table(text: str) -> None:
    assert checklist.parse_checklist(text) == []
    issues = checklist.lint_checklist(text)
    assert [(issue.level, issue.line) for issue in issues] == [("error", 1)]
    assert "table" in issues[0].message


def test_the_table_ends_at_the_first_line_that_is_not_a_row() -> None:
    text = table("| RC-01 | t | c | o |") + "Not a row.\n| RC-02 | t | c | o |\n"
    assert [item.id for item in checklist.parse_checklist(text)] == ["RC-01"]


def test_parse_skips_rows_that_do_not_have_four_cells() -> None:
    text = table("| RC-01 | t | c | o |", "| RC-02 | t | c |", "| RC-03 | t | c | o | extra |", "| RC-04 | t | c | o |")
    assert [item.id for item in checklist.parse_checklist(text)] == ["RC-01", "RC-04"]


def test_crlf_line_endings() -> None:
    text = table("| RC-01 | t | c | o |").replace("\n", "\r\n")
    assert checklist.parse_checklist(text) == [ChecklistItem("RC-01", "t", "c", "o", 3)]
    assert checklist.lint_checklist(text) == []


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        # Clean.
        (["| RC-01 | t | c | o |", "| RC-02 | t | c | o |"], []),
        (["| RC-001 | t | c | o |", "| RC-1234 | t | c | o |"], []),
        # Wrong number of cells.
        (["| RC-01 | t | c |"], [("error", 3)]),
        (["| RC-01 | t | c | o | extra |"], [("error", 3)]),
        (["| RC-01 | a | b unescaped | pipe | o |"], [("error", 3)]),
        (["|"], [("error", 3)]),
        # Ids.
        (["| RC-1 | t | c | o |"], [("error", 3)]),
        (["| rc-01 | t | c | o |"], [("error", 3)]),
        (["| RC-0A | t | c | o |"], [("error", 3)]),
        (["| XX-01 | t | c | o |"], [("error", 3)]),
        (["| RC01 | t | c | o |"], [("error", 3)]),
        (["| RC-01 extra | t | c | o |"], [("error", 3)]),
        (["|  | t | c | o |"], [("error", 3)]),
        (["| `RC-01` | t | c | o |"], [("error", 3)]),
        # Duplicates are reported on the later row.
        (["| RC-01 | t | c | o |", "| RC-02 | t | c | o |", "| RC-01 | t2 | c2 | o2 |"], [("error", 5)]),
        # Empty trigger or check.
        (["| RC-01 |  | c | o |"], [("error", 3)]),
        (["| RC-01 | t |  | o |"], [("error", 3)]),
        (["| RC-01 |  |  | o |"], [("error", 3), ("error", 3)]),
        # Origins that are missing or still a placeholder only warn.
        (["| RC-01 | t | c |  |"], [("warning", 3)]),
        (["| RC-01 | t | c | PR #___ |"], [("warning", 3)]),
        (["| RC-01 | t | c | ___ |"], [("warning", 3)]),
        (["| RC-01 | t | c | snake_case_name |"], []),
        # An empty table is allowed, with a warning on the header line.
        ([], [("warning", 1)]),
        # Several problems, in line order.
        (
            ["| RC-01 | t | c | ___ |", "| RC-1 | t | c | o |", "| RC-01 |  | c | o |"],
            [("warning", 3), ("error", 4), ("error", 5), ("error", 5)],
        ),
    ],
)
def test_lint(rows, expected) -> None:
    assert lint(table(*rows)) == expected


def test_lint_messages_say_what_is_wrong() -> None:
    text = table(
        "| RC-01 | t | c | o |",
        "| RC-01 | t | c | o |",
        "| RC-2 | t | c | o |",
        "| RC-03 |  | c | o |",
        "| RC-04 | t |  | o |",
        "| RC-05 | t | c |  |",
        "| RC-06 | t | c | ___ |",
        "| RC-07 | t | c |",
    )
    messages = {issue.line: issue.message for issue in checklist.lint_checklist(text)}
    assert "duplicate" in messages[4] and "line 3" in messages[4]
    assert "RC-" in messages[5]
    assert "trigger" in messages[6]
    assert "check" in messages[7]
    assert "origin" in messages[8]
    assert "placeholder" in messages[9]
    assert "4 cells" in messages[10]


def test_lint_reports_line_numbers_of_the_file_not_of_the_table() -> None:
    text = "# Title\n\nSome prose.\n\n" + table("| RC-01 | t | c | o |", "| bad | t | c | o |")
    (issue,) = checklist.lint_checklist(text)
    assert isinstance(issue, LintIssue)
    assert (issue.level, issue.line) == ("error", 8)


def test_an_escaped_pipe_is_one_cell() -> None:
    text = table("| RC-01 | `x \\| y` and a \\| b | c \\| d | o |")
    assert checklist.parse_checklist(text) == [ChecklistItem("RC-01", "`x | y` and a | b", "c | d", "o", 3)]
    assert checklist.lint_checklist(text) == []


def test_a_long_untrusted_id_is_not_echoed_whole() -> None:
    text = table("| " + "X" * 500 + " | t | c | o |")
    (issue,) = checklist.lint_checklist(text)
    assert issue.level == "error" and len(issue.message) < 200
