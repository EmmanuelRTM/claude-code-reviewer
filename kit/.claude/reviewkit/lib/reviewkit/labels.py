"""The labels the kit owns, and the few label operations the gates need.

Labels are the kit's visible state on issues and pull requests: the risk tier,
"a person is needed", "this issue has no acceptance criteria". They are created
on first use so a repository needs no setup step.

GitHub treats label names as case-insensitive (`Risk:High` and `risk:high` are
one label), so every comparison here ignores case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from .github.client import GitHubError, GitHubPort


@dataclass(frozen=True)
class LabelSpec:
    """How a label looks: a six-digit hex color (no `#`) and a one-line description."""

    color: str
    description: str


KIT_LABELS: Dict[str, LabelSpec] = {
    "risk:low": LabelSpec("0e8a16", "Low risk: docs, copy, internal-only or flagged additive changes"),
    "risk:medium": LabelSpec("fbca04", "Medium risk: ordinary feature and bug-fix work"),
    "risk:high": LabelSpec("d93f0b", "High risk: auth, data access, billing, migrations, CI workflows, review rules"),
    "needs-human": LabelSpec("b60205", "Automated review rounds are exhausted; a person needs to look"),
    "needs-acceptance-criteria": LabelSpec("c5def5", "The issue has no checkable acceptance criteria yet"),
    "review-metrics": LabelSpec("5319e7", "Monthly report on how the review process is performing"),
    "claude-review:always": LabelSpec("1d76db", "Claude reviews every push to this pull request"),
}


def label_names(labels: Optional[Iterable[Any]]) -> List[str]:
    """Names from a REST `labels` array (objects with a `name`) or from plain strings."""
    names: List[str] = []
    for label in labels or ():
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str) and name:
            names.append(name)
    return names


def has_label(names: Iterable[str], wanted: str) -> bool:
    """Whether `wanted` is among `names`, compared the way GitHub compares labels."""
    return any(name.lower() == wanted.lower() for name in names)


def ensure_label(gh: GitHubPort, owner: str, repo: str, name: str, color: str, description: str) -> bool:
    """Create a repository label unless it exists. Returns True when it was created.

    GitHub answers 422 when the label already exists; that is success here,
    and an existing label keeps whatever color and description people gave it.
    """
    try:
        gh.post(f"/repos/{owner}/{repo}/labels", {"name": name, "color": color, "description": description})
    except GitHubError as err:
        if err.status == 422:
            return False
        raise
    return True


def ensure_kit_label(gh: GitHubPort, owner: str, repo: str, name: str) -> bool:
    """`ensure_label` for one of the kit's own labels, styled from `KIT_LABELS`."""
    spec = KIT_LABELS[name]
    return ensure_label(gh, owner, repo, name, spec.color, spec.description)


def add_label(gh: GitHubPort, owner: str, repo: str, number: int, name: str) -> None:
    """Put one label on an issue or pull request (adding a present label is harmless)."""
    gh.post(f"/repos/{owner}/{repo}/issues/{number}/labels", {"labels": [name]})


def remove_label(gh: GitHubPort, owner: str, repo: str, number: int, name: str) -> bool:
    """Take one label off an issue or pull request. Returns False when it was already gone."""
    try:
        gh.delete(f"/repos/{owner}/{repo}/issues/{number}/labels/{quote(name, safe='')}")
    except GitHubError as err:
        if err.status == 404:
            return False
        raise
    return True


def set_exclusive_label(
    gh: GitHubPort,
    owner: str,
    repo: str,
    number: int,
    prefix: str,
    wanted: Optional[str],
    current_labels: Iterable[Any],
) -> Tuple[List[str], List[str]]:
    """Make `wanted` the only label starting with `prefix` on issue or pull request `number`.

    `current_labels` is what is on it now (names or REST label objects).
    `wanted=None` just removes the group. Returns `(added, removed)`.

    The wanted label is added before the others are removed, so a failure half
    way leaves two labels of the group rather than none: callers that read the
    group back (the risk tier takes the highest) then err on the safe side.
    """
    current = label_names(current_labels)
    added: List[str] = []
    removed: List[str] = []
    if wanted is not None and not has_label(current, wanted):
        add_label(gh, owner, repo, number, wanted)
        added.append(wanted)
    for name in current:
        if not name.lower().startswith(prefix.lower()):
            continue
        if wanted is not None and name.lower() == wanted.lower():
            continue
        if remove_label(gh, owner, repo, number, name):
            removed.append(name)
    return added, removed
