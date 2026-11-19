"""Normalize the reviewer's structured output into findings that are safe to publish.

The model judges; this code decides what is allowed onto the pull request:

* every field is re-validated and sanitized (the output is untrusted),
* a finding must quote code that really is in the file, and its line is snapped
  to where that code is (a location that cannot be verified is dropped),
* duplicates, earlier still-open findings and anything over the caps are
  counted but not posted,
* each finding is routed inline (its line is in the diff) or to the summary.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..paths import normalize_path
from ..sanitize import clean_text, one_line
from .diffmap import FileDiff
from .schema import PRIOR_STATUSES, SEVERITIES

SEVERITY_RANK = {"important": 0, "nit": 1, "pre_existing": 2}
COUNT_KEYS = {"important": "normal", "nit": "nit", "pre_existing": "pre_existing"}

_TITLE_LIMIT, _BODY_LIMIT, _EVIDENCE_LIMIT, _QUOTE_LIMIT, _NOTE_LIMIT = 200, 2000, 2000, 600, 500
_QUOTE_WINDOW = 15
_MIN_DISTINCTIVE = 4
_PRIOR_LINE_SLACK = 3
_MAX_REJECTED = 100
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Finding:
    id: str
    path: str
    line: int
    severity: str
    title: str
    body: str
    evidence: str
    quote: str
    lens: str
    checklist_id: str = ""
    in_scope: bool = True
    inline: bool = False
    comment_path: Optional[str] = None
    comment_line: Optional[int] = None
    comment_start_line: Optional[int] = None


@dataclass
class Outcome:
    valid: bool = True
    error: str = ""
    summary: str = ""
    posted: List[Finding] = field(default_factory=list)
    suppressed: Dict[str, int] = field(
        default_factory=lambda: {"duplicates": 0, "out_of_scope": 0, "nits": 0, "pre_existing": 0, "over_limit": 0}
    )
    dropped: List[dict] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)
    prior: Dict[str, dict] = field(default_factory=dict)
    coverage: Dict[str, Any] = field(default_factory=dict)
    missing_lenses: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.valid and not self.missing_lenses


def _squash(text: str) -> str:
    return " ".join(text.split())


def locate_quote(content: str, quote: str, line: int, window: int = _QUOTE_WINDOW) -> Optional[int]:
    """Line number where the first line of `quote` appears, nearest to `line`; None if absent."""
    needle = next((_squash(part) for part in quote.split("\n") if part.strip()), "")
    if not needle:
        return None
    exact = len(needle) < _MIN_DISTINCTIVE
    matches = [
        number
        for number, text in enumerate(content.split("\n"), start=1)
        if (_squash(text) == needle if exact else needle in _squash(text))
    ]
    if not matches:
        return None
    nearby = [number for number in matches if abs(number - line) <= window]
    if nearby:
        return min(nearby, key=lambda number: (abs(number - line), number))
    return matches[0] if len(matches) == 1 else None


def _finding_id(path: str, title: str, quote: str, salt: str = "") -> str:
    title_key = " ".join(_WORD.findall(title.lower()))
    quote_key = _squash(quote.split("\n")[0]) if quote else ""
    return hashlib.sha1(f"{path}\n{title_key}\n{quote_key}\n{salt}".encode()).hexdigest()[:10]  # noqa: S324


def _positive_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _validate(raw: Mapping, diff: Mapping[str, FileDiff], read_file: Callable[[str], Optional[str]]):
    """Return `(Finding, None)` or `(None, reason)` for one raw finding."""
    try:
        path = normalize_path(raw.get("path")) if isinstance(raw.get("path"), str) else None
    except ValueError:
        path = None
    if path is None:
        return None, "invalid path"
    line = _positive_int(raw.get("line"))
    if line is None:
        return None, "invalid line"
    severity = raw.get("severity")
    if severity not in SEVERITIES:
        return None, "invalid severity"
    title = one_line(raw.get("title"), _TITLE_LIMIT)
    if not title:
        return None, "missing title"
    entry = diff.get(path)
    content = read_file(path)
    if content is None and entry is None:
        return None, "file is not part of the pull request head"
    quote = raw.get("quote") if isinstance(raw.get("quote"), str) else ""
    if content is not None:
        if not quote.strip():
            return None, "no quote of the code the finding points at"
        located = locate_quote(content, quote, line)
        if located is None:
            return None, "quoted code not found in the file"
        line = located

    comment_path = comment_line = comment_start = None
    if entry is not None and entry.commentable(line):
        comment_path, comment_line = path, line
        start = _positive_int(raw.get("start_line"))
        if start is not None and start < line and entry.range_commentable(start, line):
            comment_start = start
    else:
        cause_line = _positive_int(raw.get("cause_line"))
        try:
            cause_path = normalize_path(raw["cause_path"]) if isinstance(raw.get("cause_path"), str) else None
        except ValueError:
            cause_path = None
        cause_entry = diff.get(cause_path) if cause_path else None
        if cause_entry is not None and cause_line is not None and cause_entry.commentable(cause_line):
            comment_path, comment_line = cause_path, cause_line

    return (
        Finding(
            id=_finding_id(path, title, quote),
            path=path,
            line=line,
            severity=severity,
            title=title,
            body=clean_text(raw.get("body"), _BODY_LIMIT),
            evidence=clean_text(raw.get("evidence"), _EVIDENCE_LIMIT),
            quote=clean_text(quote, _QUOTE_LIMIT),
            lens=one_line(raw.get("lens"), 40),
            checklist_id=one_line(raw.get("checklist_id"), 20),
            in_scope=raw.get("in_scope") is not False,
            inline=comment_line is not None,
            comment_path=comment_path,
            comment_line=comment_line,
            comment_start_line=comment_start,
        ),
        None,
    )


def _dedupe(findings: Sequence[Finding], prior_open: Sequence[Mapping], suppressed: Dict[str, int]) -> List[Finding]:
    best: Dict[tuple, Finding] = {}
    for item in findings:
        key = (item.path, item.line, " ".join(_WORD.findall(item.title.lower())))
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        suppressed["duplicates"] += 1
        if SEVERITY_RANK[item.severity] < SEVERITY_RANK[current.severity]:
            best[key] = item
    unique: List[Finding] = []
    for item in best.values():
        repeats_prior = any(
            prior.get("path") == item.path
            and prior.get("severity") == item.severity
            and isinstance(prior.get("line"), int)
            and abs(prior["line"] - item.line) <= _PRIOR_LINE_SLACK
            for prior in prior_open
        )
        if repeats_prior:
            suppressed["duplicates"] += 1
        else:
            unique.append(item)
    return unique


def _unique_ids(findings: Sequence[Finding]) -> List[Finding]:
    seen: set = set()
    result: List[Finding] = []
    for item in findings:
        identifier = item.id
        if identifier in seen:
            identifier = _finding_id(item.path, item.title, item.quote, salt=str(item.line))
        seen.add(identifier)
        result.append(item if identifier == item.id else Finding(**{**item.__dict__, "id": identifier}))
    return result


def _apply_scope_and_caps(
    findings: Sequence[Finding], mode: str, max_findings: int, max_nits: int, suppressed: Dict[str, int]
) -> List[Finding]:
    nit_cap = max_nits if mode == "full" else (0 if mode == "escalation" else math.ceil(max_nits / 2))
    kept: List[Finding] = []
    nits = 0
    for item in sorted(findings, key=lambda f: (SEVERITY_RANK[f.severity], f.path, f.line, f.id)):
        if mode != "full" and not item.in_scope:
            suppressed["out_of_scope"] += 1
        elif item.severity == "pre_existing" and mode != "full":
            suppressed["pre_existing"] += 1
        elif item.severity == "nit" and nits >= nit_cap:
            suppressed["nits"] += 1
        elif len(kept) >= max_findings:
            suppressed["over_limit"] += 1
        else:
            nits += item.severity == "nit"
            kept.append(item)
    return kept


def normalize(
    raw: Any,
    *,
    diff: Mapping[str, FileDiff],
    read_file: Callable[[str], Optional[str]],
    prior_open: Sequence[Mapping],
    mode: str,
    max_findings: int,
    max_nits: int,
    planned_lenses: Sequence[str],
) -> Outcome:
    if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
        return Outcome(valid=False, error="the review did not return a findings list")
    outcome = Outcome(summary=clean_text(raw.get("summary"), 3000))

    validated: List[Finding] = []
    for item in raw["findings"]:
        finding, reason = _validate(item, diff, read_file) if isinstance(item, dict) else (None, "not an object")
        if finding is None:
            source = item if isinstance(item, dict) else {}
            outcome.dropped.append(
                {
                    "title": one_line(source.get("title"), _TITLE_LIMIT),
                    "path": one_line(source.get("path"), 300),
                    "line": source.get("line") if isinstance(source.get("line"), int) else None,
                    "reason": reason,
                }
            )
        else:
            validated.append(finding)

    unique = _unique_ids(_dedupe(validated, prior_open, outcome.suppressed))
    outcome.posted = _apply_scope_and_caps(unique, mode, max_findings, max_nits, outcome.suppressed)

    known_prior = {str(prior.get("id")) for prior in prior_open}
    for entry in raw.get("prior") if isinstance(raw.get("prior"), list) else []:
        if isinstance(entry, dict) and entry.get("id") in known_prior and entry.get("status") in PRIOR_STATUSES:
            outcome.prior[entry["id"]] = {"status": entry["status"], "note": clean_text(entry.get("note"), _NOTE_LIMIT)}

    for entry in (raw.get("rejected") if isinstance(raw.get("rejected"), list) else [])[:_MAX_REJECTED]:
        if isinstance(entry, dict):
            outcome.rejected.append(
                {
                    "path": one_line(entry.get("path"), 300),
                    "line": entry.get("line") if isinstance(entry.get("line"), int) else None,
                    "title": one_line(entry.get("title"), _TITLE_LIMIT),
                    "reason": one_line(entry.get("reason"), 300),
                    "lens": one_line(entry.get("lens"), 40),
                }
            )

    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    completed = [one_line(name, 40) for name in coverage.get("lenses_completed") or [] if isinstance(name, str)]
    outcome.missing_lenses = [name for name in planned_lenses if name not in completed]
    outcome.coverage = {
        "lenses_planned": list(planned_lenses),
        "lenses_completed": completed,
        "candidates": coverage.get("candidates") if isinstance(coverage.get("candidates"), int) else None,
        "confirmed": coverage.get("confirmed") if isinstance(coverage.get("confirmed"), int) else None,
        "checklist_items": [
            one_line(name, 20) for name in coverage.get("checklist_items") or [] if isinstance(name, str)
        ],
        "notes": clean_text(coverage.get("notes"), _NOTE_LIMIT),
    }
    return outcome


def severity_counts(findings: Sequence[Any]) -> Dict[str, int]:
    """Counts keyed like the managed service's machine-readable line (`normal` = Important)."""
    counts = {"nit": 0, "normal": 0, "pre_existing": 0}
    for item in findings:
        severity = item.severity if hasattr(item, "severity") else item.get("severity")
        if severity in COUNT_KEYS:
            counts[COUNT_KEYS[severity]] += 1
    return counts
