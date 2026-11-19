"""The reliability checklist: a Markdown table of diff-triggered checks.

    | ID | Trigger (readable from a diff) | Check | Origin |
    | --- | --- | --- | --- |
    | RC-01 | A new outbound HTTP call | It sets a timeout | Incident 2025-03 |

The reviewer cites rows by id, the metrics report counts those citations, and
this module is the one place that knows how the table is laid out. The linter
keeps the file machine-readable: a row that does not parse is a check that
silently never runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

ID_PATTERN = re.compile(r"RC-\d{2,}\Z")
COLUMNS = ("ID", "Trigger", "Check", "Origin")
PLACEHOLDER = "___"

_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_SEPARATOR_CELL = re.compile(r":?-+:?\Z")


@dataclass(frozen=True)
class ChecklistItem:
    """One row of the checklist; `line` is its 1-based line number in the file."""

    id: str
    trigger: str
    check: str
    origin: str
    line: int


@dataclass(frozen=True)
class LintIssue:
    """One finding of the linter; `level` is `error` or `warning`."""

    level: str
    line: int
    message: str


@dataclass(frozen=True)
class _Table:
    header_line: int
    rows: Tuple[Tuple[int, Tuple[str, ...]], ...]  # (line number, cells)


def is_valid_id(text: str) -> bool:
    """Whether `text` is a checklist id: `RC-` followed by two or more digits."""
    return ID_PATTERN.match(text) is not None


def split_row(line: str) -> Optional[List[str]]:
    """Cells of a table row, with `\\|` unescaped; None when the line is not a row."""
    text = line.strip()
    if not text.startswith("|"):
        return None
    text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _UNESCAPED_PIPE.split(text)]


def _is_header(cells: Optional[List[str]]) -> bool:
    if cells is None or len(cells) != len(COLUMNS):
        return False
    return all(cell.lower().startswith(name.lower()) for cell, name in zip(cells, COLUMNS))


def _is_separator(cells: Optional[List[str]]) -> bool:
    if cells is None or len(cells) != len(COLUMNS):
        return False
    return all(_SEPARATOR_CELL.match(cell) for cell in cells)


def _find_table(text: str) -> Optional[_Table]:
    """The first table headed ID / Trigger / Check / Origin and followed by a separator row."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _is_header(split_row(line)):
            continue
        if index + 1 >= len(lines) or not _is_separator(split_row(lines[index + 1])):
            continue  # without a separator row Markdown does not render a table
        rows: List[Tuple[int, Tuple[str, ...]]] = []
        for offset, candidate in enumerate(lines[index + 2 :], start=index + 3):
            cells = split_row(candidate)
            if cells is None:
                break
            rows.append((offset, tuple(cells)))
        return _Table(index + 1, tuple(rows))
    return None


def parse_checklist(text: str) -> List[ChecklistItem]:
    """The rows of the checklist table, in file order.

    Rows that do not have exactly four cells are left out here and reported
    by `lint_checklist`. No table means no items.
    """
    table = _find_table(text)
    if table is None:
        return []
    return [
        ChecklistItem(cells[0], cells[1], cells[2], cells[3], line)
        for line, cells in table.rows
        if len(cells) == len(COLUMNS)
    ]


def lint_checklist(text: str) -> List[LintIssue]:
    """Everything wrong with the checklist, in line order.

    Errors make the file unusable for a row or as a whole. Warnings (an origin
    that is empty or still a `___` placeholder, a table without rows) are
    worth fixing but do not stop the reviewer from using the checklist.
    """
    table = _find_table(text)
    if table is None:
        message = "no checklist table found: expected a Markdown table headed | ID | Trigger | Check | Origin |"
        return [LintIssue("error", 1, message)]
    issues: List[LintIssue] = []
    if not table.rows:
        issues.append(LintIssue("warning", table.header_line, "the checklist table has no rows"))
    first_seen: Dict[str, int] = {}
    for line, cells in table.rows:
        if len(cells) != len(COLUMNS):
            message = f"expected 4 cells (ID, Trigger, Check, Origin) but found {len(cells)}; write a literal | as \\|"
            issues.append(LintIssue("error", line, message))
            continue
        identifier, trigger, check, origin = cells
        if not is_valid_id(identifier):
            shown = identifier if len(identifier) <= 40 else identifier[:40] + "..."
            issues.append(LintIssue("error", line, f"id {shown!r} must look like RC-01 (RC- and two or more digits)"))
        if identifier in first_seen:
            message = f"duplicate id {identifier[:40]!r} (first used on line {first_seen[identifier]})"
            issues.append(LintIssue("error", line, message))
        elif identifier:
            first_seen[identifier] = line
        if not trigger:
            issues.append(LintIssue("error", line, "the trigger is empty"))
        if not check:
            issues.append(LintIssue("error", line, "the check is empty"))
        if not origin:
            issues.append(LintIssue("warning", line, "the origin is empty: say where this check came from"))
        elif PLACEHOLDER in origin:
            issues.append(LintIssue("warning", line, "the origin is still a placeholder"))
    return issues
