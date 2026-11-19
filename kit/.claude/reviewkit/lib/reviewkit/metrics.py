"""Review the reviewers: numbers that say whether the review process earns its keep.

Collection (`collect_pulls`, the only function here that talks to GitHub)
turns a time window into plain records, one per merged pull request.
Everything else is pure computation over those records, so each number can be
checked against hand-built data.

A value that cannot be computed (a rate over zero pull requests) is `None`,
never `0`: the report must not show a made-up zero.

Text people wrote (titles, bodies, commit messages, comments) is only ever
classified and counted. The one free-form value kept for the report, a
checklist id cited by a finding, must match the strict id pattern first.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import markers
from .approvals import is_agent, parse_timestamp
from .checklist import is_valid_id
from .github.client import GitHubPort
from .labels import label_names

DEFAULT_DAYS = 30
MAX_PULLS = 200
OUTCOMES: Tuple[str, ...] = ("useful", "wrong", "not_worth_it", "intentional", "unrated")
SEVERITIES: Tuple[str, ...] = ("important", "nit", "pre_existing")
UNKNOWN_SEVERITY = "unknown"
MAX_ID_LENGTH = 16

# First match wins: a finding someone called wrong is wrong, however many others liked it.
_REACTION_OUTCOMES: Tuple[Tuple[str, str], ...] = (
    ("-1", "wrong"),
    ("confused", "not_worth_it"),
    ("eyes", "intentional"),
    ("+1", "useful"),
)
_ROUND_LABEL = re.compile(r"review-round:(\d{1,6})\Z")
_TRAILER = re.compile(r"^verified-by:[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)
_OPT_OUT = re.compile(r"none\b", re.IGNORECASE)
_VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")


@dataclass(frozen=True)
class RevertStats:
    """How many merged pull requests were reverts; `rate` is None when nothing was merged."""

    merged: int
    reverts: int
    rate: Optional[float]


@dataclass(frozen=True)
class RoundStats:
    """Review rounds per reviewed pull request; the three ratios are None when none was reviewed."""

    reviewed: int
    not_reviewed: int
    mean: Optional[float]
    one_round_share: Optional[float]
    five_plus_share: Optional[float]


@dataclass(frozen=True)
class FindingStats:
    """What people said about the review findings.

    `cells[severity][outcome]` is the full cross table and `by_outcome`,
    `by_severity` and `total` are its margins. `precision` is None until at
    least one finding was rated useful, wrong or not worth it.
    `by_checklist_id` counts findings per cited checklist item and `uncited`
    those that cite none (or something that is not a well-formed id).
    """

    total: int
    by_outcome: Dict[str, int]
    by_severity: Dict[str, int]
    cells: Dict[str, Dict[str, int]]
    precision: Optional[float]
    by_checklist_id: Dict[str, int]
    uncited: int


@dataclass(frozen=True)
class TrailerStats:
    """Commits of the merged pull requests by their `Verified-By:` trailer."""

    commits: int
    verified: int
    opted_out: int
    missing: int


@dataclass(frozen=True)
class ApprovalStats:
    """Human approvals; the mean is None without pull requests, the median without any approval."""

    pulls: int
    approved_pulls: int
    mean_per_pull: Optional[float]
    median_hours_to_first: Optional[float]


@dataclass(frozen=True)
class Metrics:
    """Every section of the report for one window."""

    reverts: RevertStats
    rounds: RoundStats
    findings: FindingStats
    trailers: TrailerStats
    approvals: ApprovalStats


@dataclass(frozen=True)
class TrailerVerdict:
    """`status` is `verified`, `opted_out` or `missing`; `reason` is the text in parentheses after `none`."""

    status: str
    reason: str = ""


def _share(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


# -- collection (I/O) ---------------------------------------------------------


def window(days: int, today: date) -> Tuple[date, date]:
    """`(start, end)` of a report covering the `days` days up to `today`."""
    return today - timedelta(days=days), today


def collect_pulls(gh: GitHubPort, owner: str, repo: str, since: date, limit: int = MAX_PULLS) -> List[Dict[str, Any]]:
    """One record per pull request merged on or after `since`, at most `limit` of them.

    Newest first by creation, so a window that hits the limit keeps the most
    recent pull requests. Each record holds what the computation needs:
    `number`, `title`, `body`, `user`, `created_at`, `merged_at`, `labels`
    (names), `reviews`, `review_comments` and `commits` (REST shapes).
    """
    query = f"repo:{owner}/{repo} is:pr is:merged merged:>={since.isoformat()}"
    params = {"q": query, "sort": "created", "order": "desc"}
    found = gh.get_all("/search/issues", params, items_key="items", limit=limit)
    records: List[Dict[str, Any]] = []
    seen = set()
    for item in found:
        number = item.get("number") if isinstance(item, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number in seen:
            continue
        seen.add(number)
        base = f"/repos/{owner}/{repo}/pulls/{number}"
        pull = gh.get(base) or {}
        records.append(
            {
                "number": number,
                "title": pull.get("title") or "",
                "body": pull.get("body") or "",
                "user": pull.get("user"),
                "created_at": pull.get("created_at"),
                "merged_at": pull.get("merged_at"),
                "labels": label_names(item.get("labels")),
                "reviews": gh.get_all(f"{base}/reviews"),
                "review_comments": gh.get_all(f"{base}/comments"),
                "commits": gh.get_all(f"{base}/commits"),
            }
        )
    return records


# -- reverts ------------------------------------------------------------------


def is_revert(title: Optional[str], body: Optional[str]) -> bool:
    """Whether a merged pull request undoes an earlier one."""
    start = (title or "").lstrip().lower()
    return start.startswith(('revert "', "revert:")) or "This reverts commit" in (body or "")


def revert_stats(pulls: Sequence[Mapping[str, Any]]) -> RevertStats:
    """Revert rate: a high one means review let through changes that had to be undone."""
    reverts = sum(1 for pull in pulls if is_revert(pull.get("title"), pull.get("body")))
    return RevertStats(len(pulls), reverts, _share(reverts, len(pulls)))


# -- rounds to pass -----------------------------------------------------------


def rounds_from_labels(labels: Iterable[str]) -> Optional[int]:
    """The highest N among `review-round:<N>` labels (N of 1 or more), or None."""
    rounds: List[int] = []
    for name in labels:
        match = _ROUND_LABEL.match(name.strip().lower()) if isinstance(name, str) else None
        if match and int(match.group(1)) >= 1:
            rounds.append(int(match.group(1)))
    return max(rounds) if rounds else None


def round_stats(pulls: Sequence[Mapping[str, Any]]) -> RoundStats:
    """Rounds to pass: how much back and forth the automated review costs."""
    found = [rounds_from_labels(pull.get("labels") or ()) for pull in pulls]
    rounds = [value for value in found if value is not None]
    return RoundStats(
        reviewed=len(rounds),
        not_reviewed=len(found) - len(rounds),
        mean=sum(rounds) / len(rounds) if rounds else None,
        one_round_share=_share(sum(1 for value in rounds if value == 1), len(rounds)),
        five_plus_share=_share(sum(1 for value in rounds if value >= 5), len(rounds)),
    )


# -- finding outcomes and checklist usage -------------------------------------


def finding_outcome(reactions: Any) -> str:
    """What people said about a finding by reacting to its comment."""
    if isinstance(reactions, Mapping):
        for key, outcome in _REACTION_OUTCOMES:
            count = reactions.get(key)
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                return outcome
    return "unrated"


def _finding(comment: Any) -> Optional[Dict[str, Any]]:
    """The finding marker of a review comment written by a bot, if it has one."""
    if not isinstance(comment, Mapping):
        return None
    user, body = comment.get("user"), comment.get("body")
    if not isinstance(user, Mapping) or user.get("type") != "Bot" or not isinstance(body, str):
        return None
    return markers.decode_last("finding", body)


def _checklist_id(payload: Mapping[str, Any]) -> Optional[str]:
    cid = payload.get("cid")
    return cid if isinstance(cid, str) and len(cid) <= MAX_ID_LENGTH and is_valid_id(cid) else None


def finding_stats(pulls: Sequence[Mapping[str, Any]]) -> FindingStats:
    """Finding outcomes and checklist usage.

    A finding is a review comment written by a bot account that carries a
    `finding` marker. Comments by people are never findings, whatever they
    contain, so nobody can add to these numbers by pasting a marker.
    """
    severities = SEVERITIES + (UNKNOWN_SEVERITY,)
    cells = {severity: dict.fromkeys(OUTCOMES, 0) for severity in severities}
    by_checklist_id: Dict[str, int] = {}
    uncited = 0
    for pull in pulls:
        for comment in pull.get("review_comments") or ():
            payload = _finding(comment)
            if payload is None:
                continue
            severity = payload.get("sev")
            row = cells[severity if isinstance(severity, str) and severity in SEVERITIES else UNKNOWN_SEVERITY]
            row[finding_outcome(comment.get("reactions"))] += 1
            cid = _checklist_id(payload)
            if cid is None:
                uncited += 1
            else:
                by_checklist_id[cid] = by_checklist_id.get(cid, 0) + 1
    by_outcome = {outcome: sum(row[outcome] for row in cells.values()) for outcome in OUTCOMES}
    by_severity = {severity: sum(cells[severity].values()) for severity in severities}
    rated = by_outcome["useful"] + by_outcome["wrong"] + by_outcome["not_worth_it"]
    return FindingStats(
        total=sum(by_severity.values()),
        by_outcome=by_outcome,
        by_severity=by_severity,
        cells=cells,
        precision=_share(by_outcome["useful"], rated),
        by_checklist_id=by_checklist_id,
        uncited=uncited,
    )


# -- verification trailers ----------------------------------------------------


def classify_trailer(message: Optional[str]) -> TrailerVerdict:
    """Classify a commit by the last `Verified-By:` trailer line of its message."""
    values = _TRAILER.findall((message or "").replace("\r\n", "\n"))
    value = values[-1].strip() if values else ""
    if not value:
        return TrailerVerdict("missing")
    if _OPT_OUT.match(value):
        start = value.find("(")
        end = value.find(")", start + 1) if start >= 0 else -1
        return TrailerVerdict("opted_out", value[start + 1 : end].strip() if end >= 0 else "")
    return TrailerVerdict("verified")


def trailer_stats(pulls: Sequence[Mapping[str, Any]]) -> TrailerStats:
    """Verification trailers over every commit of every pull request in the window."""
    counts = {"verified": 0, "opted_out": 0, "missing": 0}
    for pull in pulls:
        for entry in pull.get("commits") or ():
            commit = entry.get("commit") if isinstance(entry, Mapping) else None
            message = commit.get("message") if isinstance(commit, Mapping) else None
            counts[classify_trailer(message if isinstance(message, str) else None).status] += 1
    return TrailerStats(sum(counts.values()), counts["verified"], counts["opted_out"], counts["missing"])


# -- human approvals ----------------------------------------------------------


def _human_approvals(reviews: Iterable[Any], agent_logins: Sequence[str]) -> Dict[str, List[Any]]:
    """Per person whose latest verdict approves: the times of their approving reviews."""
    verdicts: Dict[str, List[Tuple[Any, int, str]]] = {}
    for index, review in enumerate(reviews):
        user = review.get("user") if isinstance(review, Mapping) else None
        login = user.get("login") if isinstance(user, Mapping) else None
        state = str(review.get("state") or "").upper() if isinstance(review, Mapping) else ""
        if not isinstance(login, str) or not login or state not in _VERDICTS:
            continue
        if is_agent(login, user.get("type"), agent_logins):
            continue
        when = parse_timestamp(review.get("submitted_at"))
        verdicts.setdefault(login.lower(), []).append((when, index, state))
    approving: Dict[str, List[Any]] = {}
    for login, entries in verdicts.items():
        # Undated reviews keep GitHub's order (oldest first) relative to each other.
        ordered = sorted(entries, key=lambda entry: (entry[0] is not None, entry[0] or 0, entry[1]))
        if ordered[-1][2] == "APPROVED":
            approving[login] = [when for when, _, state in ordered if state == "APPROVED" and when is not None]
    return approving


def approval_stats(pulls: Sequence[Mapping[str, Any]], agent_logins: Sequence[str]) -> ApprovalStats:
    """Human approvals per pull request and how long the first one took.

    A person approves when their latest review with a verdict is `APPROVED`.
    Bots and the accounts in `agent_logins` are not people.
    """
    approvers = 0
    approved_pulls = 0
    hours: List[float] = []
    for pull in pulls:
        approving = _human_approvals(pull.get("reviews") or (), agent_logins)
        approvers += len(approving)
        approved_pulls += 1 if approving else 0
        created = parse_timestamp(pull.get("created_at"))
        times = [when for whens in approving.values() for when in whens]
        if created is not None and times:
            hours.append(max(0.0, (min(times) - created).total_seconds() / 3600))
    return ApprovalStats(
        pulls=len(pulls),
        approved_pulls=approved_pulls,
        mean_per_pull=approvers / len(pulls) if pulls else None,
        median_hours_to_first=statistics.median(hours) if hours else None,
    )


def compute_metrics(pulls: Sequence[Mapping[str, Any]], agent_logins: Sequence[str] = ()) -> Metrics:
    """All sections of the report from the records of one window."""
    return Metrics(
        reverts=revert_stats(pulls),
        rounds=round_stats(pulls),
        findings=finding_stats(pulls),
        trailers=trailer_stats(pulls),
        approvals=approval_stats(pulls, agent_logins),
    )
