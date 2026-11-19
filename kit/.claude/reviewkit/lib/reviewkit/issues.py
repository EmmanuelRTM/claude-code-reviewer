"""Issue intake: read a task issue's form answers and label what is missing.

GitHub renders an issue form into the issue body as, per field:

    ### <Field label>

    <answer, or _No response_ when left empty>

The intake reads two answers, the risk tier and the acceptance criteria. The
body is written by whoever opened the issue, so it is only ever read: nothing
from it is copied into a label, a comment or an output. The risk label is
built from a fixed vocabulary and the reminder comment is fixed wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import markers
from .github.client import GitHubPort
from .labels import add_label, ensure_kit_label, has_label, label_names, remove_label, set_exclusive_label
from .risk import LABEL_PREFIX, TIERS, tier_label

FIELD_RISK = "risk tier"
FIELD_ACCEPTANCE = "acceptance criteria"
NO_RESPONSE = "_No response_"
NEEDS_CRITERIA_LABEL = "needs-acceptance-criteria"
INTAKE_MARKER = "intake"

INTAKE_COMMENT = "\n".join(
    [
        "This issue has no acceptance criteria yet.",
        "",
        "Acceptance criteria are short, checkable statements that say when the work is done. The change is "
        "verified and reviewed against them, so work on this issue should wait until they exist.",
        "",
        "Please edit the issue description and add them under an `### Acceptance criteria` heading, one "
        "checkbox per statement:",
        "",
        "```",
        "### Acceptance criteria",
        "",
        "- [ ] The first result someone can observe or test",
        "- [ ] The second result someone can observe or test",
        "```",
        "",
        f"The `{NEEDS_CRITERIA_LABEL}` label is removed automatically once they are there.",
        "",
        markers.encode(INTAKE_MARKER),
    ]
)

# Issue bodies are written by anyone, so every pattern here runs in linear time.
_HEADING = re.compile(r" {0,3}###(?!#)[ \t]+(\S.*)\Z")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)\Z")
_LIST_AND_CHECKBOX = re.compile(r"\s*(?:[-*+]|\d+[.)])?\s*(?:\[[ xX]?\])?")
_FIRST_WORD = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class IntakeResult:
    """What the form says: the chosen risk tier, if any, and whether criteria exist."""

    risk: Optional[str]
    has_acceptance_criteria: bool


@dataclass(frozen=True)
class IntakeOutcome:
    """What `apply_intake` found and changed on one issue."""

    result: IntakeResult
    added: Tuple[str, ...]
    removed: Tuple[str, ...]
    commented: bool


def _opening_fence(line: str) -> Optional[Tuple[str, int]]:
    """`(character, length)` when the line opens a fenced code block."""
    match = _FENCE.match(line)
    if not match:
        return None
    ticks, info = match.group(1), match.group(2)
    if ticks[0] == "`" and "`" in info:
        return None  # inline code such as ```x``` on one line, not a fence
    return ticks[0], len(ticks)


def _closes(line: str, fence: Tuple[str, int]) -> bool:
    match = _FENCE.match(line)
    if not match or match.group(2).strip():
        return False
    ticks = match.group(1)
    return ticks[0] == fence[0] and len(ticks) >= fence[1]


def parse_issue_form(body: Optional[str]) -> Dict[str, str]:
    """Map each lower-cased field label to its trimmed answer (`""` for no response).

    Tolerates CRLF line endings and extra blank lines. A `###` heading inside
    a fenced code block is part of an answer, not a field. When a label
    appears twice the first one wins, so text typed into a later field cannot
    replace an earlier answer.
    """
    collected: Dict[str, List[str]] = {}
    current: Optional[str] = None
    fence: Optional[Tuple[str, int]] = None
    for line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence is not None:
            if _closes(line, fence):
                fence = None
        else:
            fence = _opening_fence(line)
            heading = None if fence else _HEADING.match(line)
            if heading:
                label = " ".join(heading.group(1).lower().split())
                current = None if label in collected else label
                if current is not None:
                    collected[current] = []
                continue
        if current is not None:
            collected[current].append(line)
    return {label: _answer(lines) for label, lines in collected.items()}


def _answer(lines: List[str]) -> str:
    text = "\n".join(lines).strip()
    return "" if text == NO_RESPONSE else text


def _risk(answer: str) -> Optional[str]:
    """The first word of the answer when it names a tier (`High`, `High: ...`, `**High**`)."""
    match = _FIRST_WORD.search(answer)
    word = match.group(0).lower() if match else ""
    return word if word in TIERS else None


def _without_html_comments(text: str) -> str:
    """`text` minus its `<!-- ... -->` comments, which nobody sees in the rendered issue."""
    kept: List[str] = []
    position = 0
    while True:
        start = text.find("<!--", position)
        end = text.find("-->", start + 4) if start >= 0 else -1
        if end < 0:
            break
        kept.append(text[position:start])
        position = end + 3
    kept.append(text[position:])
    return "".join(kept)


def _has_criteria(answer: str) -> bool:
    """True when at least one line says something beyond a bullet or an empty checkbox."""
    for line in _without_html_comments(answer).splitlines():
        rest = _LIST_AND_CHECKBOX.match(line)
        remainder = line[rest.end() :] if rest else line
        if any(character.isalnum() for character in remainder):
            return True
    return False


def intake(body: Optional[str]) -> IntakeResult:
    """Read the risk tier and the acceptance criteria out of an issue body."""
    fields = parse_issue_form(body)
    return IntakeResult(
        risk=_risk(fields.get(FIELD_RISK, "")),
        has_acceptance_criteria=_has_criteria(fields.get(FIELD_ACCEPTANCE, "")),
    )


def _is_reminder(comment: Any) -> bool:
    """Whether a comment is this intake's reminder: written by a bot and carrying the marker."""
    if not isinstance(comment, Mapping):
        return False
    user, body = comment.get("user"), comment.get("body")
    if not isinstance(user, Mapping) or not isinstance(body, str):
        return False
    return user.get("type") == "Bot" and markers.has(INTAKE_MARKER, body)


def _remind_once(gh: GitHubPort, owner: str, repo: str, number: int) -> bool:
    """Post the reminder unless a bot already posted it. Returns True when it was posted."""
    comments = gh.get_all(f"/repos/{owner}/{repo}/issues/{number}/comments")
    if any(_is_reminder(comment) for comment in comments):
        return False
    gh.post(f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": INTAKE_COMMENT})
    return True


def apply_intake(gh: GitHubPort, owner: str, repo: str, issue: Mapping[str, Any]) -> Optional[IntakeOutcome]:
    """Label an issue from its form answers; ask once for missing acceptance criteria.

    `issue` is the issue object of an `issues` event. Pull requests (objects
    with a `pull_request` key) are ignored and yield None.
    """
    if "pull_request" in issue:
        return None
    number = issue["number"]
    current = label_names(issue.get("labels"))
    result = intake(issue.get("body"))
    added: List[str] = []
    removed: List[str] = []
    commented = False

    if result.risk is not None:
        wanted = tier_label(result.risk)
        if not has_label(current, wanted):
            ensure_kit_label(gh, owner, repo, wanted)
        more_added, more_removed = set_exclusive_label(gh, owner, repo, number, LABEL_PREFIX, wanted, current)
        added += more_added
        removed += more_removed

    flagged = has_label(current, NEEDS_CRITERIA_LABEL)
    if result.has_acceptance_criteria:
        if flagged and remove_label(gh, owner, repo, number, NEEDS_CRITERIA_LABEL):
            removed.append(NEEDS_CRITERIA_LABEL)
    else:
        if not flagged:
            ensure_kit_label(gh, owner, repo, NEEDS_CRITERIA_LABEL)
            add_label(gh, owner, repo, number, NEEDS_CRITERIA_LABEL)
            added.append(NEEDS_CRITERIA_LABEL)
        commented = _remind_once(gh, owner, repo, number)
    return IntakeOutcome(result, tuple(added), tuple(removed), commented)
