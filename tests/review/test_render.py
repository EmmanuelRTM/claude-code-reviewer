"""Markdown the reviewer posts: inline comments, the review summary, the check run."""

from __future__ import annotations

import json
import re

from reviewkit import markers
from reviewkit.review import render
from reviewkit.review.findings import Finding, Outcome
from reviewkit.review.plan import Plan
from reviewkit.review.state import ReviewState, latest_state


def finding(severity="important", inline=True, **overrides):
    base = {
        "id": "ab12cd34ef",
        "path": "src/auth/session.ts",
        "line": 142,
        "severity": severity,
        "title": "Token refresh races with logout",
        "body": "A logout between the check and the write leaves a live session.",
        "evidence": "`refresh()` writes the token at line 150 without re-checking `loggedOut`.",
        "quote": "session.token = token",
        "lens": "correctness",
        "checklist_id": "RC-03",
        "inline": inline,
        "comment_path": "src/auth/session.ts" if inline else None,
        "comment_line": 142 if inline else None,
    }
    base.update(overrides)
    return Finding(**base)


PLAN = Plan(
    run=True,
    reason="run",
    pr_number=7,
    head_sha="abc1234def5678",
    mode="full",
    round=2,
    level="standard",
    lenses=("correctness",),
    publish="advisory",
)


def test_inline_comment_has_severity_title_body_reasoning_and_a_marker():
    text = render.render_comment(finding(), round_number=2)
    lines = text.split("\n")
    assert lines[0] == "🔴 **Important**: Token refresh races with logout"
    assert "A logout between the check and the write" in text
    assert "<details><summary>Why this was flagged</summary>" in text
    assert markers.decode_last("finding", text) == {"id": "ab12cd34ef", "sev": "important", "round": 2, "cid": "RC-03"}


def test_other_severities_and_a_finding_commented_on_its_cause():
    assert render.render_comment(finding("nit"), 1).startswith("🟡 **Nit**: ")
    assert render.render_comment(finding("pre_existing"), 1).startswith("🟣 **Pre-existing**: ")
    moved = finding(path="src/auth/logout.ts", line=9, comment_path="src/auth/session.ts", comment_line=142)
    assert "`src/auth/logout.ts:9`" in render.render_comment(moved, 1)


def test_severity_line_matches_the_managed_service_format_exactly():
    line = render.severity_line({"normal": 2, "nit": 1, "pre_existing": 0})
    assert line == '<!-- bughunter-severity: {"nit":1,"normal":2,"pre_existing":0} -->'
    # The documented jq recipe: the last line, scanned with this expression, parsed as JSON.
    match = re.match(r"^<!-- bughunter-severity: (.*) -->$", line)
    assert json.loads(match.group(1)) == {"nit": 1, "normal": 2, "pre_existing": 0}


def test_review_body_starts_with_counts_and_carries_state():
    outcome = Outcome(summary="Two problems in session handling.", posted=[finding(), finding("nit", id="n1")])
    outcome.suppressed.update({"out_of_scope": 2, "nits": 1, "pre_existing": 3})
    state = ReviewState(round=2, head="abc1234def5678", patch="p", open_important=("ab12cd34ef",))
    body = render.render_review_body(
        outcome,
        PLAN,
        state,
        prior_open_counts={"normal": 1, "nit": 0, "pre_existing": 0},
        escalated=False,
        run_url="https://example.invalid/run/1",
        max_rounds=3,
    )
    first = body.split("\n")[0]
    assert first == "### Claude review, round 2: 🔴 1 Important · 🟡 1 Nit · 🟣 0 Pre-existing"
    assert "Two problems in session handling." in body
    assert (
        "2 findings on code that has not changed since the last review were outside the scope of this re-review and not posted."
        in body
    )
    assert "4 nits and pre-existing findings were found and not posted." in body
    assert "Still open from earlier rounds" in body
    assert "abc1234" in body and "https://example.invalid/run/1" in body
    restored = latest_state(
        [{"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}}], [], "github-actions"
    )
    assert restored == state


def test_review_body_with_no_findings_and_with_summary_only_findings():
    clean = render.render_review_body(Outcome(summary="Looks correct."), PLAN, ReviewState(round=2), {}, False, "", 3)
    assert clean.split("\n")[0] == "### Claude review, round 2: no issues found"
    outcome = Outcome(
        posted=[finding("pre_existing", inline=False, line=88, title="parseExpiry returns 0 | on bad input")]
    )
    body = render.render_review_body(outcome, PLAN, ReviewState(round=2), {}, False, "", 3)
    assert "| 🟣 Pre-existing | `src/auth/session.ts:88` | parseExpiry returns 0 \\| on bad input |" in body


def test_review_body_flags_incomplete_coverage_and_escalation():
    outcome = Outcome(posted=[finding()], missing_lenses=["contracts"])
    body = render.render_review_body(outcome, PLAN, ReviewState(round=3), {}, True, "", 3)
    assert "Incomplete review" in body and "`contracts`" in body
    assert "Needs a human" in body and "3 review rounds" in body


def test_check_output_table_is_sorted_and_ends_with_the_severity_line():
    outcome = Outcome(posted=[finding("nit", id="n1", line=88, inline=False), finding()])
    outcome.dropped.append({"title": "ghost", "path": "x.py", "line": 1, "reason": "quoted code not found in the file"})
    check = render.render_check(outcome, PLAN, open_counts={"normal": 1, "nit": 1, "pre_existing": 0}, prior_open=())
    assert check.title == "Code review found 2 issues"
    rows = [line for line in check.text.split("\n") if line.startswith("| 🔴") or line.startswith("| 🟡")]
    assert rows[0].startswith("| 🔴 Important | `src/auth/session.ts:142` |")
    assert rows[1].startswith("| 🟡 Nit | `src/auth/session.ts:88` |")
    assert (
        check.text.rstrip("\n").split("\n")[-1] == '<!-- bughunter-severity: {"nit":1,"normal":1,"pre_existing":0} -->'
    )
    assert "quoted code not found" in check.text
    # Findings that could not be posted inline become annotations on the Files changed tab.
    assert check.annotations == [
        {
            "path": "src/auth/session.ts",
            "start_line": 88,
            "end_line": 88,
            "annotation_level": "warning",
            "title": "Nit: Token refresh races with logout",
            "message": "A logout between the check and the write leaves a live session.",
        }
    ]


def test_check_output_for_a_clean_review_and_singular_wording():
    clean = render.render_check(Outcome(), PLAN, {"normal": 0, "nit": 0, "pre_existing": 0}, ())
    assert clean.title == "Code review found no issues"
    one = render.render_check(Outcome(posted=[finding()]), PLAN, {"normal": 1, "nit": 0, "pre_existing": 0}, ())
    assert one.title == "Code review found 1 issue"


def test_check_output_never_exceeds_github_limits_and_keeps_the_last_line():
    many = [finding(id=f"id{i:08d}", line=i + 1, title="t" * 150, inline=False) for i in range(600)]
    check = render.render_check(Outcome(posted=many), PLAN, {"normal": 600, "nit": 0, "pre_existing": 0}, ())
    assert len(check.text) <= 65_000
    assert check.text.rstrip("\n").split("\n")[-1].startswith("<!-- bughunter-severity: ")
    assert len(check.annotations) <= 50


def test_skip_and_failure_check_outputs():
    kept = render.render_skip_check("Code review skipped — no new changes", "Same lines.", with_counts=True)
    assert (
        kept.text.rstrip("\n").split("\n")[-1] == '<!-- bughunter-severity: {"nit":0,"normal":0,"pre_existing":0} -->'
    )
    not_repeated = render.render_skip_check(
        "Code review not repeated — earlier findings still open", "Open.", with_counts=False
    )
    assert "bughunter-severity" not in not_repeated.text
    failed = render.render_failure_check("the review session did not return a result", "https://example.invalid/run/1")
    assert failed.title == "Code review did not complete" and "bughunter-severity" not in failed.text
    assert "https://example.invalid/run/1" in failed.summary
