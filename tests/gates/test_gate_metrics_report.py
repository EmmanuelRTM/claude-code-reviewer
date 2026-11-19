"""Rendering the monthly review metrics report."""

from __future__ import annotations

import re
from datetime import date
from typing import Dict, List

import pytest

from gates.gates_kit import commit, finding, review
from gates.gates_kit import hours_after_creation as hours
from gates.gates_kit import merged_pull as record
from reviewkit import metrics
from reviewkit.checklist import ChecklistItem
from reviewkit.metrics_report import render_report, report_title

START, END = date(2026, 2, 1), date(2026, 3, 3)
ITEMS = [ChecklistItem(f"RC-0{n}", "trigger", "check", "origin", n + 2) for n in (1, 2, 3, 4)]
SECTIONS = [
    "Revert rate",
    "Rounds to pass",
    "Finding outcomes",
    "Checklist usage",
    "Verification trailers",
    "Human approvals",
    "Prune candidates",
]


def busy_window() -> metrics.Metrics:
    pulls = [
        record(
            1,
            labels=["review-round:1"],
            reviews=[review("alice", at=hours(2))],
            review_comments=[
                finding("important", "RC-01", {"+1": 1}),
                finding("important", "RC-01", {"+1": 1}),
                finding("important", None, {"-1": 1}),
                finding("nit", "RC-02", {"confused": 1}),
            ],
            commits=[commit("a\n\nVerified-By: verify"), commit("b")],
        ),
        record(
            2,
            labels=["review-round:1"],
            reviews=[review("alice", at=hours(4)), review("bob", at=hours(5))],
            review_comments=[finding("nit", None, {"eyes": 1}), finding("pre_existing", "RC-09", {})],
            commits=[commit("c\n\nVerified-By: none (docs only)"), commit("d\n\nVerified-By: verify")],
        ),
        record(3, labels=["review-round:3"], title='Revert "Add the export button"'),
        record(4),
    ]
    return metrics.compute_metrics(pulls)


def sections(report: str) -> Dict[str, str]:
    """Map each `## heading` to the text below it."""
    parts = re.split(r"^## (.+)$", report, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def rows(section: str) -> List[List[str]]:
    """The body rows of the first table in a section."""
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]


def test_report_title() -> None:
    assert report_title(START, END) == "Review metrics: 2026-02-01 to 2026-03-03"


def test_the_report_opens_with_its_title_and_lists_the_sections_in_order() -> None:
    report = render_report(busy_window(), START, END, ITEMS)
    assert report.startswith("# Review metrics: 2026-02-01 to 2026-03-03\n")
    assert re.findall(r"^## (.+)$", report, flags=re.MULTILINE) == SECTIONS
    assert report.endswith("\n") and not report.endswith("\n\n")


def test_every_metric_section_has_a_table() -> None:
    found = sections(render_report(busy_window(), START, END, ITEMS))
    for name in SECTIONS[:-1]:
        lines = [line for line in found[name].splitlines() if line.startswith("|")]
        assert len(lines) >= 3, name
        assert re.fullmatch(r"\|( --- \|)+", lines[1]), name
        widths = {line.count("|") for line in lines}
        assert len(widths) == 1, name


def test_revert_and_round_sections() -> None:
    found = sections(render_report(busy_window(), START, END, ITEMS))
    assert rows(found["Revert rate"]) == [
        ["Merged pull requests", "4"],
        ["Reverts", "1"],
        ["Revert rate", "25.0%"],
    ]
    assert rows(found["Rounds to pass"]) == [
        ["Pull requests reviewed", "3"],
        ["Pull requests not reviewed", "1"],
        ["Mean rounds to pass", "1.67"],
        ["Passed in one round", "66.7%"],
        ["Needed five or more rounds", "0.0%"],
    ]


def test_finding_outcomes_cross_severity_and_outcome() -> None:
    section = sections(render_report(busy_window(), START, END, ITEMS))["Finding outcomes"]
    header = [cell.strip() for cell in section.strip().splitlines()[0].strip("|").split("|")]
    assert header == ["Severity", "Useful", "Wrong", "Not worth it", "Intentional", "Unrated", "Total"]
    assert rows(section) == [
        ["important", "2", "1", "0", "0", "0", "3"],
        ["nit", "0", "0", "1", "1", "0", "2"],
        ["pre_existing", "0", "0", "0", "0", "1", "1"],
        ["All findings", "2", "1", "1", "1", "1", "6"],
    ]
    assert "Precision among rated findings: 50.0%" in section


def test_findings_with_an_unknown_severity_get_their_own_row() -> None:
    data = metrics.compute_metrics([record(1, review_comments=[finding("blocker", None, {"+1": 1})])])
    section = sections(render_report(data, START, END, ITEMS))["Finding outcomes"]
    assert ["unknown", "1", "0", "0", "0", "0", "1"] in rows(section)


def test_checklist_usage_lists_cited_items_most_used_first() -> None:
    section = sections(render_report(busy_window(), START, END, ITEMS))["Checklist usage"]
    assert rows(section) == [
        ["RC-01", "2", "yes"],
        ["RC-02", "1", "yes"],
        ["RC-09", "1", "no"],
        ["No checklist item cited", "2", "-"],
    ]


def test_checklist_usage_orders_by_count_before_id() -> None:
    comments = [finding(cid="RC-01")] + [finding(cid="RC-04")] * 3 + [finding(cid="RC-03")] * 2 + [finding(cid="RC-02")]
    data = metrics.compute_metrics([record(1, review_comments=comments)])
    section = sections(render_report(data, START, END, ITEMS))["Checklist usage"]
    assert [row[:2] for row in rows(section)][:4] == [["RC-04", "3"], ["RC-03", "2"], ["RC-01", "1"], ["RC-02", "1"]]


def test_trailer_and_approval_sections() -> None:
    found = sections(render_report(busy_window(), START, END, ITEMS))
    assert rows(found["Verification trailers"]) == [
        ["Commits", "4"],
        ["Verified", "2 (50.0%)"],
        ["Opted out", "1 (25.0%)"],
        ["No trailer", "1 (25.0%)"],
    ]
    assert rows(found["Human approvals"]) == [
        ["Mean human approvals per pull request", "0.75"],
        ["Pull requests with a human approval", "2 of 4"],
        ["Median hours to the first human approval", "3.0"],
    ]


def test_prune_candidates_are_the_checklist_items_without_findings() -> None:
    section = sections(render_report(busy_window(), START, END, ITEMS))["Prune candidates"]
    assert re.findall(r"^- (.+)$", section, flags=re.MULTILINE) == ["RC-03", "RC-04"]


def test_no_prune_candidates_when_every_item_was_used() -> None:
    used = [item for item in ITEMS if item.id in ("RC-01", "RC-02")]
    section = sections(render_report(busy_window(), START, END, used))["Prune candidates"]
    assert "- RC" not in section
    assert "None" in section


def test_prune_candidates_need_a_checklist() -> None:
    section = sections(render_report(busy_window(), START, END, []))["Prune candidates"]
    assert "- RC" not in section and "n/a" in section


def test_malformed_checklist_ids_are_never_printed() -> None:
    odd = [ChecklistItem("<b>RC</b>", "t", "c", "o", 3), ChecklistItem("RC-05", "t", "c", "o", 4)]
    report = render_report(busy_window(), START, END, odd)
    assert "<b>" not in report
    assert re.findall(r"^- (.+)$", sections(report)["Prune candidates"], flags=re.MULTILINE) == ["RC-05"]


def test_an_empty_window_shows_na_and_never_a_made_up_zero() -> None:
    report = render_report(metrics.compute_metrics([]), START, END, ITEMS)
    found = sections(report)
    assert rows(found["Revert rate"]) == [["Merged pull requests", "0"], ["Reverts", "0"], ["Revert rate", "n/a"]]
    assert rows(found["Rounds to pass"])[2:] == [
        ["Mean rounds to pass", "n/a"],
        ["Passed in one round", "n/a"],
        ["Needed five or more rounds", "n/a"],
    ]
    assert "Precision among rated findings: n/a" in found["Finding outcomes"]
    assert rows(found["Verification trailers"]) == [
        ["Commits", "0"],
        ["Verified", "0 (n/a)"],
        ["Opted out", "0 (n/a)"],
        ["No trailer", "0 (n/a)"],
    ]
    assert rows(found["Human approvals"]) == [
        ["Mean human approvals per pull request", "n/a"],
        ["Pull requests with a human approval", "0 of 0"],
        ["Median hours to the first human approval", "n/a"],
    ]
    # With nothing reviewed there is no evidence against any checklist item.
    assert "- RC" not in found["Prune candidates"] and "n/a" in found["Prune candidates"]
    assert "0.0%" not in report and "0.00" not in report
    # No unformatted Python value leaked into a table cell either.
    cells = [cell for name in SECTIONS[:-1] for row in rows(found[name]) for cell in row]
    assert not [cell for cell in cells if re.search(r"\b(None|nan|inf)\b", cell)]


def test_a_window_without_findings_does_not_nominate_the_whole_checklist() -> None:
    data = metrics.compute_metrics([record(1, labels=["review-round:1"])])
    section = sections(render_report(data, START, END, ITEMS))["Prune candidates"]
    assert "- RC" not in section and "n/a" in section


def test_notes_appear_before_the_first_section() -> None:
    report = render_report(busy_window(), START, END, ITEMS, notes=["Only the first 200 were analyzed."])
    assert "Only the first 200 were analyzed." in report.split("## ")[0]


def test_the_report_is_plain_text_without_trailing_whitespace() -> None:
    report = render_report(busy_window(), START, END, ITEMS)
    assert report.isascii()
    assert all(line == line.rstrip() for line in report.splitlines())
    assert "\n\n\n" not in report


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_real_zeroes_and_full_shares_are_printed_as_numbers(value: float) -> None:
    data = metrics.compute_metrics([record(1, title='Revert "x"' if value else "Add x")])
    section = sections(render_report(data, START, END, ITEMS))["Revert rate"]
    assert rows(section)[2] == ["Revert rate", f"{value * 100:.1f}%"]
