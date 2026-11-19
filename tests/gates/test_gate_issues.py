"""Issue forms: reading the rendered answers and deciding what the intake needs."""

from __future__ import annotations

import time

import pytest

from reviewkit import issues

FORM = """### Summary

Add an export button to the orders page.

### Acceptance criteria

- [ ] The button downloads a CSV with one row per order
- [ ] An empty list downloads a file with only the header

### Risk tier

Medium

### Verification plan

_No response_

### Notes

_No response_
"""


def form(criteria: str = "- [ ] It works end to end", risk: str = "Medium") -> str:
    return f"### Summary\n\nSomething\n\n### Acceptance criteria\n\n{criteria}\n\n### Risk tier\n\n{risk}\n"


def test_parse_reads_every_field_of_a_rendered_form() -> None:
    assert issues.parse_issue_form(FORM) == {
        "summary": "Add an export button to the orders page.",
        "acceptance criteria": (
            "- [ ] The button downloads a CSV with one row per order\n"
            "- [ ] An empty list downloads a file with only the header"
        ),
        "risk tier": "Medium",
        "verification plan": "",
        "notes": "",
    }


def test_parse_tolerates_crlf_line_endings() -> None:
    assert issues.parse_issue_form(FORM.replace("\n", "\r\n")) == issues.parse_issue_form(FORM)
    assert issues.parse_issue_form(FORM.replace("\n", "\r")) == issues.parse_issue_form(FORM)


def test_parse_tolerates_extra_blank_lines_and_trailing_spaces() -> None:
    body = "\n\n###   Risk Tier  \n\n\n\n  High  \n\n\n\n### Notes\n\n\n"
    assert issues.parse_issue_form(body) == {"risk tier": "High", "notes": ""}


@pytest.mark.parametrize(
    "fenced",
    [
        "```\n### Risk tier\n\nLow\n```",
        "```markdown\n### Risk tier\n\nLow\n```",
        "~~~\n### Risk tier\n\nLow\n~~~",
        "````\n```\n### Risk tier\n\nLow\n```\n````",
        "   ```\n### Risk tier\n\nLow\n   ```",
        # A shorter or different fence does not close the block.
        "````\n### Risk tier\n```\nLow\n~~~\n````",
    ],
)
def test_headings_inside_fenced_code_are_not_fields(fenced: str) -> None:
    body = f"### Summary\n\nSee this example:\n\n{fenced}\n\nDone.\n\n### Risk tier\n\nHigh\n"
    fields = issues.parse_issue_form(body)
    assert set(fields) == {"summary", "risk tier"}
    assert fields["risk tier"] == "High"
    assert fields["summary"].startswith("See this example:") and fields["summary"].endswith("Done.")
    assert "### Risk tier" in fields["summary"]


def test_an_unclosed_fence_runs_to_the_end_like_markdown_renders_it() -> None:
    body = "### Summary\n\n```\nnever closed\n\n### Risk tier\n\nHigh\n"
    assert set(issues.parse_issue_form(body)) == {"summary"}


def test_inline_code_is_not_a_fence() -> None:
    body = "### Summary\n\n```not a fence``` because it closes on the same line\n\n### Risk tier\n\nLow\n"
    assert issues.parse_issue_form(body)["risk tier"] == "Low"


@pytest.mark.parametrize(
    "line",
    ["#### Risk tier", "## Risk tier", "# Risk tier", "###Risk tier", "    ### Risk tier", "text ### Risk tier", "###"],
)
def test_only_level_three_headings_start_a_field(line: str) -> None:
    body = f"### Summary\n\nfirst\n{line}\n\nsecond\n"
    assert set(issues.parse_issue_form(body)) == {"summary"}


def test_text_before_the_first_heading_is_not_a_field() -> None:
    assert issues.parse_issue_form("Free text issue.\n\nNo form here.") == {}
    assert issues.parse_issue_form("intro\n\n### Notes\n\nx") == {"notes": "x"}


def test_the_first_of_two_fields_with_the_same_label_wins() -> None:
    body = "### Risk tier\n\nHigh\n\n### Notes\n\nfine\n\n### Risk tier\n\nLow\n\nextra\n"
    assert issues.parse_issue_form(body) == {"risk tier": "High", "notes": "fine"}


@pytest.mark.parametrize("body", ["", None, "\n\n", "### \n\nx"])
def test_parse_handles_empty_input(body) -> None:
    assert issues.parse_issue_form(body) == {}


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Low", "low"),
        ("Medium", "medium"),
        ("High", "high"),
        ("high", "high"),
        ("HIGH", "high"),
        ("High: auth and billing", "high"),
        ("Low (docs only)", "low"),
        ("**Medium**", "medium"),
        ("- High", "high"),
        ("_No response_", None),
        ("", None),
        ("None", None),
        ("Critical", None),
        ("Highest", None),
        ("Not high", None),
        ("3", None),
    ],
)
def test_intake_reads_the_risk_tier_from_the_first_word(answer: str, expected) -> None:
    assert issues.intake(form(risk=answer)).risk == expected


def test_intake_without_a_risk_field_has_no_risk() -> None:
    assert issues.intake("### Summary\n\nx\n").risk is None
    assert issues.intake("").risk is None
    assert issues.intake(None).risk is None


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("- [ ] The button downloads a CSV", True),
        ("- [x] Already done part", True),
        ("* [ ] Star bullets work too", True),
        ("1. [ ] Numbered", True),
        ("Plain sentence without a checkbox.", True),
        ("- [ ]\n- [ ] The second line is real", True),
        ("- [ ] 42", True),
        # Nothing but empty checkboxes, bullets or punctuation.
        ("- [ ]", False),
        ("- [ ] ", False),
        ("- [ ]\n- [ ]\n- [ ]", False),
        ("- [x]", False),
        ("- [X]   ", False),
        ("* [ ]", False),
        ("1. [ ]", False),
        ("[ ]", False),
        ("- []", False),
        ("-", False),
        ("- [ ] ...", False),
        ("---", False),
        ("_No response_", False),
        ("", False),
        ("   \n\n  ", False),
        # Text nobody can see is not a criterion.
        ("<!-- one checkbox per criterion -->", False),
        ("<!-- one checkbox\nper criterion -->\n- [ ]", False),
        ("<!-- hint -->\n- [ ] A real one", True),
    ],
)
def test_acceptance_criteria_need_a_line_with_real_text(answer: str, expected: bool) -> None:
    assert issues.intake(form(criteria=answer)).has_acceptance_criteria is expected


def test_an_unclosed_html_comment_hides_nothing() -> None:
    assert issues.intake(form(criteria="<!-- never closed\n- [ ] Real criterion")).has_acceptance_criteria is True
    assert issues.intake(form(criteria="a --> b <!-- x --> <!-- y -->")).has_acceptance_criteria is True
    assert issues.intake(form(criteria="<!-- x --><!-- y -->\n<!---->")).has_acceptance_criteria is False


def test_hostile_bodies_are_parsed_in_linear_time() -> None:
    """An issue body can be 65536 characters of anything; parsing it must not take noticeable time."""
    size = 65_536
    hostile = [
        "### a" + " " * size + "b",
        "### Acceptance criteria\n\n" + "<!--" * (size // 4),
        "### Acceptance criteria\n\n" + "- [ ] " * (size // 6),
        "### Risk tier\n\n" + " " * size + "High",
        "```" * (size // 3),
        ("### x\n" * (size // 6)),
    ]
    started = time.perf_counter()
    for body in hostile:
        issues.intake(body)
        issues.parse_issue_form(body)
    assert time.perf_counter() - started < 2.0


def test_intake_without_the_field_has_no_acceptance_criteria() -> None:
    assert issues.intake("### Summary\n\nJust do the thing.\n").has_acceptance_criteria is False
    assert issues.intake("A free-form issue with no form at all.").has_acceptance_criteria is False


def test_intake_of_the_full_form() -> None:
    assert issues.intake(FORM) == issues.IntakeResult(risk="medium", has_acceptance_criteria=True)


def test_field_labels_are_the_lower_cased_form_labels() -> None:
    assert issues.FIELD_RISK == "risk tier"
    assert issues.FIELD_ACCEPTANCE == "acceptance criteria"
