"""Render review metrics as the Markdown report that is posted as a monthly issue.

Plain statements and one table per section. A value that could not be
computed is printed as `n/a`, never as zero. Nothing people wrote is printed:
the report is built from counts, fixed wording and checklist ids that match
the strict id pattern.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

from .checklist import ChecklistItem, is_valid_id
from .metrics import OUTCOMES, SEVERITIES, UNKNOWN_SEVERITY, Metrics

NOT_AVAILABLE = "n/a"
_OUTCOME_TITLES = {
    "useful": "Useful",
    "wrong": "Wrong",
    "not_worth_it": "Not worth it",
    "intentional": "Intentional",
    "unrated": "Unrated",
}


def report_title(start_date: Any, end_date: Any) -> str:
    """Title of the report and of the issue that carries it."""
    return f"Review metrics: {start_date} to {end_date}"


def _percent(value: Optional[float]) -> str:
    return NOT_AVAILABLE if value is None else f"{value * 100:.1f}%"


def _number(value: Optional[float], digits: int) -> str:
    return NOT_AVAILABLE if value is None else f"{value:.{digits}f}"


def _count_with_share(count: int, total: int) -> str:
    return f"{count} ({_percent(count / total if total else None)})"


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> List[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def _section(title: str, table: List[str], *notes: str) -> List[str]:
    lines = [f"## {title}", ""] + table
    for note in notes:
        lines += ["", note]
    return lines + [""]


def _reverts(metrics: Metrics) -> List[str]:
    stats = metrics.reverts
    rows = [
        ("Merged pull requests", stats.merged),
        ("Reverts", stats.reverts),
        ("Revert rate", _percent(stats.rate)),
    ]
    note = (
        'A revert is a merged pull request whose title starts with `Revert "` or `Revert:`, '
        "or whose description says `This reverts commit`."
    )
    return _section("Revert rate", _table(("Measure", "Value"), rows), note)


def _rounds(metrics: Metrics) -> List[str]:
    stats = metrics.rounds
    rows = [
        ("Pull requests reviewed", stats.reviewed),
        ("Pull requests not reviewed", stats.not_reviewed),
        ("Mean rounds to pass", _number(stats.mean, 2)),
        ("Passed in one round", _percent(stats.one_round_share)),
        ("Needed five or more rounds", _percent(stats.five_plus_share)),
    ]
    note = (
        "Rounds are read from the highest `review-round:<N>` label on a pull request. "
        "A pull request without that label counts as not reviewed."
    )
    return _section("Rounds to pass", _table(("Measure", "Value"), rows), note)


def _findings(metrics: Metrics) -> List[str]:
    stats = metrics.findings
    severities = list(SEVERITIES)
    if stats.by_severity.get(UNKNOWN_SEVERITY):
        severities.append(UNKNOWN_SEVERITY)
    rows: List[List[Any]] = [
        [severity] + [stats.cells[severity][outcome] for outcome in OUTCOMES] + [stats.by_severity[severity]]
        for severity in severities
    ]
    rows.append(["All findings"] + [stats.by_outcome[outcome] for outcome in OUTCOMES] + [stats.total])
    headers = ["Severity"] + [_OUTCOME_TITLES[outcome] for outcome in OUTCOMES] + ["Total"]
    precision = (
        f"Precision among rated findings: {_percent(stats.precision)} (useful / (useful + wrong + not worth it))."
    )
    legend = (
        "People rate a finding by reacting to its comment: thumbs up means useful, thumbs down means wrong, "
        "confused means not worth it, eyes means intentional. When a comment has several kinds of reaction, "
        "the first one present in this order decides: thumbs down, confused, eyes, thumbs up."
    )
    return _section("Finding outcomes", _table(headers, rows), precision, legend)


def _checklist_usage(metrics: Metrics, known_ids: Sequence[str]) -> List[str]:
    stats = metrics.findings
    cited = sorted(stats.by_checklist_id.items(), key=lambda pair: (-pair[1], pair[0]))
    rows: List[Sequence[Any]] = [
        (identifier, count, "yes" if identifier in known_ids else "no") for identifier, count in cited
    ]
    rows.append(("No checklist item cited", stats.uncited, "-"))
    note = (
        "A finding counts toward the checklist item it cites. "
        "An item that is cited but not in the checklist was removed or misnamed."
    )
    return _section("Checklist usage", _table(("Checklist item", "Findings", "In the checklist"), rows), note)


def _trailers(metrics: Metrics) -> List[str]:
    stats = metrics.trailers
    rows = [
        ("Commits", stats.commits),
        ("Verified", _count_with_share(stats.verified, stats.commits)),
        ("Opted out", _count_with_share(stats.opted_out, stats.commits)),
        ("No trailer", _count_with_share(stats.missing, stats.commits)),
    ]
    note = (
        "Each commit of the merged pull requests is classified by its last `Verified-By:` trailer: "
        "`none (...)` is an opt-out, any other value counts as verified."
    )
    return _section("Verification trailers", _table(("Measure", "Value"), rows), note)


def _approvals(metrics: Metrics) -> List[str]:
    stats = metrics.approvals
    rows = [
        ("Mean human approvals per pull request", _number(stats.mean_per_pull, 2)),
        ("Pull requests with a human approval", f"{stats.approved_pulls} of {stats.pulls}"),
        ("Median hours to the first human approval", _number(stats.median_hours_to_first, 1)),
    ]
    note = (
        "A human approval is a person, not a bot or a configured agent, whose latest review approves. "
        "Hours run from the creation of the pull request to the first such approval."
    )
    return _section("Human approvals", _table(("Measure", "Value"), rows), note)


def _prune_candidates(metrics: Metrics, known_ids: Sequence[str]) -> List[str]:
    lines = ["## Prune candidates", ""]
    if not known_ids:
        return lines + [f"{NOT_AVAILABLE}: no checklist items were found to compare against."]
    if metrics.findings.total == 0:
        return lines + [
            f"{NOT_AVAILABLE}: no review findings were recorded, "
            "so this window says nothing about any single checklist item."
        ]
    unused = [identifier for identifier in known_ids if identifier not in metrics.findings.by_checklist_id]
    if not unused:
        return lines + ["None. Every checklist item produced at least one finding in this window."]
    lines += [
        "These checklist items produced no findings in this window. "
        "That is a reason to look at each one, not proof that it is dead:",
        "",
    ]
    return lines + [f"- {identifier}" for identifier in unused]


def render_report(
    metrics: Metrics,
    start_date: Any,
    end_date: Any,
    checklist_items: Sequence[ChecklistItem],
    notes: Sequence[str] = (),
) -> str:
    """The whole report as Markdown.

    `checklist_items` are the rows of the repository's checklist; ids that
    produced no findings are listed as prune candidates. `notes` are extra
    fixed sentences placed under the introduction (for example that the
    window was cut at the pull request limit).
    """
    known_ids: List[str] = []
    for item in checklist_items:
        if is_valid_id(item.id) and item.id not in known_ids:
            known_ids.append(item.id)
    lines = [
        f"# {report_title(start_date, end_date)}",
        "",
        "This report reviews the reviewers. It is computed from the pull requests merged in this window: "
        "their labels, reviews, review comments, reactions and commit messages. "
        f"A value shown as {NOT_AVAILABLE} could not be computed because the window holds no data for it.",
        "",
    ]
    for note in notes:
        lines += [note, ""]
    lines += _reverts(metrics)
    lines += _rounds(metrics)
    lines += _findings(metrics)
    lines += _checklist_usage(metrics, known_ids)
    lines += _trailers(metrics)
    lines += _approvals(metrics)
    lines += _prune_candidates(metrics, known_ids)
    return "\n".join(lines).rstrip("\n") + "\n"
