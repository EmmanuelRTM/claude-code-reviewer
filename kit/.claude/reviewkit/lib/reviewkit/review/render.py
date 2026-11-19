"""Markdown for everything the reviewer leaves on a pull request.

The shapes follow the managed Code Review service on purpose: the same three
severities and markers, a collapsed "Why this was flagged" section, a summary
that opens with counts, and a check run whose Details end with the same
machine-readable severity line, so gating scripts and reading habits carry
over unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence

from .. import markers
from .findings import SEVERITY_RANK, Finding, Outcome
from .plan import Plan
from .state import FINDING_KIND, PriorFinding, ReviewState, state_marker

BADGE = {"important": "🔴 **Important**", "nit": "🟡 **Nit**", "pre_existing": "🟣 **Pre-existing**"}
LABEL = {"important": "🔴 Important", "nit": "🟡 Nit", "pre_existing": "🟣 Pre-existing"}
PLAIN = {"important": "Important", "nit": "Nit", "pre_existing": "Pre-existing"}
ANNOTATION_LEVEL = {"important": "failure", "nit": "warning", "pre_existing": "notice"}

MAX_CHECK_TEXT = 65_000
MAX_ANNOTATIONS = 50
REACTION_LEGEND = "React on a finding: 👍 useful · 👎 wrong · 😕 not worth it · 👀 intentional"


@dataclass
class CheckOutput:
    title: str
    summary: str
    text: str = ""
    annotations: List[dict] = field(default_factory=list)


def severity_line(counts: Mapping[str, int]) -> str:
    """The machine-readable last line of the check run's Details (keys in alphabetical order)."""
    ordered = {key: int(counts.get(key, 0)) for key in ("nit", "normal", "pre_existing")}
    return f"<!-- bughunter-severity: {json.dumps(ordered, separators=(',', ':'))} -->"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _table(rows: Sequence[Sequence[str]]) -> str:
    lines = ["| Severity | File:Line | Issue |", "| - | - | - |"]
    lines += [f"| {severity} | `{location}` | {_cell(issue)} |" for severity, location, issue in rows]
    return "\n".join(lines)


def findings_table(findings: Sequence[Finding]) -> str:
    """Every finding of a round in one table, most severe first."""
    ordered = sorted(findings, key=lambda item: (SEVERITY_RANK[item.severity], item.path, item.line))
    return _table([(LABEL[item.severity], f"{item.path}:{item.line}", item.title) for item in ordered])


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render_comment(finding: Finding, round_number: int) -> str:
    payload = {"id": finding.id, "sev": finding.severity, "round": round_number}
    if finding.checklist_id:
        payload["cid"] = finding.checklist_id
    parts = [f"{BADGE[finding.severity]}: {finding.title}"]
    elsewhere = finding.comment_path is not None and (finding.comment_path, finding.comment_line) != (
        finding.path,
        finding.line,
    )
    if elsewhere:
        parts.append(f"Affects `{finding.path}:{finding.line}`, which this change does not touch but breaks from here.")
    if finding.body:
        parts.append(finding.body)
    if finding.evidence:
        parts.append(f"<details><summary>Why this was flagged</summary>\n\n{finding.evidence}\n\n</details>")
    parts.append(markers.encode(FINDING_KIND, payload))
    return "\n\n".join(parts)


def _count_text(counts: Mapping[str, int]) -> str:
    return (
        f"🔴 {counts.get('normal', 0)} Important · 🟡 {counts.get('nit', 0)} Nit · "
        f"🟣 {counts.get('pre_existing', 0)} Pre-existing"
    )


def _counts_of(findings: Sequence[Finding]) -> dict:
    counts = {"normal": 0, "nit": 0, "pre_existing": 0}
    for item in findings:
        counts["normal" if item.severity == "important" else item.severity] += 1
    return counts


def render_review_body(
    outcome: Outcome,
    plan: Plan,
    state: ReviewState,
    prior_open_counts: Mapping[str, int],
    escalated: bool,
    run_url: str,
    max_rounds: int,
) -> str:
    """The review summary. Opens with counts by severity; ends with the hidden state marker."""
    posted = outcome.posted
    heading = _count_text(_counts_of(posted)) if posted else "no issues found"
    parts = [f"### Claude review, round {plan.round}: {heading}"]
    if outcome.summary:
        parts.append(outcome.summary)
    summary_only = [item for item in posted if not item.inline]
    if summary_only:
        rows = [(LABEL[item.severity], f"{item.path}:{item.line}", item.title) for item in summary_only]
        parts.append("Findings on lines outside this diff:\n\n" + _table(rows))
    if any(prior_open_counts.get(key) for key in ("normal", "nit", "pre_existing")):
        parts.append(f"Still open from earlier rounds: {_count_text(prior_open_counts)}")
    out_of_scope = outcome.suppressed.get("out_of_scope", 0)
    if out_of_scope:
        parts.append(
            f"{_plural(out_of_scope, 'finding')} on code that has not changed since the last review were "
            "outside the scope of this re-review and not posted."
        )
    quiet = outcome.suppressed.get("nits", 0) + outcome.suppressed.get("pre_existing", 0)
    if quiet:
        parts.append(f"{quiet} nits and pre-existing findings were found and not posted.")
    if plan.mode != "full" and (out_of_scope or quiet):
        parts.append("Comment `@claude review` for a review that covers the whole pull request again.")
    if outcome.missing_lenses:
        names = ", ".join(f"`{name}`" for name in outcome.missing_lenses)
        parts.append(
            f"> **Incomplete review:** the {names} pass did not report. "
            "Treat this as partial coverage, not a clean result."
        )
    if escalated:
        parts.append(
            f"> **Needs a human:** Important findings are still open after {max_rounds} review rounds. "
            "Stop fixing findings one by one and reconsider the approach with a person."
        )
    footer = [f"Reviewed `{plan.head_sha[:7]}`", f"level {plan.level}", f"{plan.mode} review"]
    if run_url:
        footer.append(f"[run]({run_url})")
    parts.append(f"<sub>{' · '.join(footer)}<br>{REACTION_LEGEND}</sub>")
    parts.append(state_marker(state))
    return "\n\n".join(parts)


def _annotations(findings: Sequence[Finding]) -> List[dict]:
    return [
        {
            "path": item.path,
            "start_line": item.line,
            "end_line": item.line,
            "annotation_level": ANNOTATION_LEVEL[item.severity],
            "title": f"{PLAIN[item.severity]}: {item.title}"[:255],
            "message": (item.body or item.title)[:4000],
        }
        for item in findings
        if not item.inline
    ][:MAX_ANNOTATIONS]


def _bounded(sections: Sequence[str], last_line: str) -> str:
    body = "\n\n".join(section for section in sections if section)
    budget = MAX_CHECK_TEXT - len(last_line) - 60
    if len(body) > budget:
        body = body[:budget].rsplit("\n", 1)[0] + "\n\n(truncated: see the pull request for every finding)"
    return f"{body}\n\n{last_line}" if last_line else body


def render_check(
    outcome: Outcome,
    plan: Plan,
    open_counts: Mapping[str, int],
    prior_open: Sequence[PriorFinding],
) -> CheckOutput:
    """Check-run output: every open finding in one table, sorted by severity."""
    posted = sorted(outcome.posted, key=lambda item: (SEVERITY_RANK[item.severity], item.path, item.line))
    total = len(posted)
    title = "Code review found no issues" if not total else f"Code review found {_plural(total, 'issue')}"
    if outcome.missing_lenses:
        title += " (incomplete)"
    rows = [(LABEL[item.severity], f"{item.path}:{item.line}", item.title) for item in posted]
    rows += [
        (LABEL[item.severity], f"{item.path}:{item.line or 0}", f"{item.title} (from round {item.round}, still open)")
        for item in sorted(prior_open, key=lambda item: (SEVERITY_RANK[item.severity], item.path, item.line or 0))
    ]
    sections = [_table(rows) if rows else "No open findings."]
    not_posted = {key: value for key, value in outcome.suppressed.items() if value}
    if not_posted:
        listed = ", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in sorted(not_posted.items()))
        sections.append(f"Found but not posted ({listed}).")
    if outcome.dropped:
        lines = [f"- {_cell(item.get('title') or '(untitled)')}: {item.get('reason')}" for item in outcome.dropped[:20]]
        sections.append("Dropped because they could not be verified against the code:\n" + "\n".join(lines))
    if outcome.rejected:
        sections.append(f"{_plural(len(outcome.rejected), 'candidate')} rejected by the verification step.")
    if outcome.missing_lenses:
        sections.append("Incomplete: these passes did not report: " + ", ".join(outcome.missing_lenses) + ".")
    summary = (
        f"Round {plan.round} ({plan.mode}, level {plan.level}) of `{plan.head_sha[:7]}`: "
        f"{_count_text(open_counts)} open."
    )
    return CheckOutput(title, summary, _bounded(sections, severity_line(open_counts)), _annotations(posted))


def render_skip_check(title: str, summary: str, with_counts: bool) -> CheckOutput:
    """A kept or skipped result. Only the "no new changes" case carries a severity line."""
    last = severity_line({"nit": 0, "normal": 0, "pre_existing": 0}) if with_counts else ""
    return CheckOutput(title, summary, _bounded([summary], last))


def render_failure_check(reason: str, run_url: str) -> CheckOutput:
    """No verdict: the severity line is deliberately absent, so scripts do not read it as zero findings."""
    link = f" See the [workflow run]({run_url})." if run_url else ""
    summary = (
        f"The review did not complete: {reason}.{link} "
        "This is not a clean result; request another with `@claude review`."
    )
    return CheckOutput("Code review did not complete", summary, summary)


def render_shadow_comment(plan: Plan, counts: Mapping[str, int], run_url: str, state: ReviewState) -> str:
    link = f" Results are in the [workflow run]({run_url})." if run_url else ""
    return (
        f"Claude review ran in shadow mode on `{plan.head_sha[:7]}` (round {plan.round}): {_count_text(counts)}.{link} "
        "Nothing is posted on the diff while the reviewer is being evaluated.\n\n" + state_marker(state)
    )


def prior_reply(status: str, note: str, head_sha: str) -> Optional[str]:
    """What to say on an earlier finding's thread when its status changed."""
    if status == "fixed":
        return f"Resolved in `{head_sha[:7]}`." + (f" {note}" if note else "")
    if status == "withdrawn":
        return "Withdrawn after reading the replies." + (f" {note}" if note else "")
    return None
