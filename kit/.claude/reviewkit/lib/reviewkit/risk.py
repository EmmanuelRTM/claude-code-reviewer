"""Risk tiers: how risky a pull request is, judged from the paths it touches.

The tier decides how many people must approve (see `approvals.py`). Everything
here is a pure function over plain data, so the rules can be read and tested
without GitHub:

* a file is `high` if it matches a high-risk pattern, else `low` if it matches
  a low-risk pattern, else `medium`;
* a pull request takes the highest tier among its files;
* a `risk:<tier>` label can only raise that, never lower it.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import RiskConfig
from .paths import match_any

TIERS: Tuple[str, ...] = ("low", "medium", "high")  # ascending
LABEL_PREFIX = "risk:"
DEFAULT_TIER = "medium"
MAX_LISTED_FILES = 3000  # GitHub lists at most this many files of a pull request


def highest(tiers: Iterable[str]) -> Optional[str]:
    """The highest of the given tiers, or None when there are none."""
    ranked = sorted((TIERS.index(tier) for tier in tiers), reverse=True)
    return TIERS[ranked[0]] if ranked else None


def file_tier(path: str, risk_cfg: RiskConfig) -> str:
    """Tier of one repository-relative path. High-risk patterns win over low-risk ones."""
    if match_any(risk_cfg.high_paths, path):
        return "high"
    if match_any(risk_cfg.low_paths, path):
        return "low"
    return "medium"


def pr_tier(paths: Iterable[str], risk_cfg: RiskConfig) -> str:
    """Highest tier among the changed paths; with no paths there is nothing to judge, so `medium`."""
    return highest(file_tier(path, risk_cfg) for path in paths) or DEFAULT_TIER


def required_approvals(tier: str, risk_cfg: RiskConfig) -> int:
    """How many human approvals a pull request of this tier needs."""
    if tier not in TIERS:
        raise ValueError(f"unknown risk tier: {tier!r}")
    return int(getattr(risk_cfg.approvals, tier))


def tier_label(tier: str) -> str:
    """The label that shows a tier on a pull request or issue, e.g. `risk:high`."""
    return LABEL_PREFIX + tier


def tier_from_labels(labels: Iterable[str]) -> Optional[str]:
    """Highest tier named by `risk:<tier>` labels (label names ignore case), if any."""
    found: List[str] = []
    for name in labels:
        if not isinstance(name, str):
            continue
        text = name.strip().lower()
        if text.startswith(LABEL_PREFIX) and text[len(LABEL_PREFIX) :] in TIERS:
            found.append(text[len(LABEL_PREFIX) :])
    return highest(found)


def final_tier(computed: str, labels: Iterable[str]) -> str:
    """The tier that applies: the higher of the path-derived tier and any tier label.

    A person may raise the tier by adding a higher `risk:*` label. Automation
    never lowers the tier below what a label says, and a label never lowers it
    below what the paths say.
    """
    labelled = tier_from_labels(labels)
    return highest([computed] if labelled is None else [computed, labelled]) or DEFAULT_TIER


def changed_paths(files: Iterable[Mapping[str, Any]]) -> List[str]:
    """Every path a pull request touches, from GitHub's file listing.

    A rename counts under both names: moving a file out of a high-risk
    directory is still a change to that directory.
    """
    paths: List[str] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            continue
        for key in ("filename", "previous_filename"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
    return paths


def tier_for_pull(files: Sequence[Mapping[str, Any]], changed_files: Any, risk_cfg: RiskConfig) -> str:
    """Path-derived tier of a pull request from its file listing.

    GitHub lists at most 3000 files of a pull request. `changed_files` is the
    count the pull request itself reports; when it is larger than the listing,
    or the listing is as long as GitHub lets it get, a high-risk file could
    sit in the part nobody can see, so the tier is `high` rather than whatever
    the visible files suggest.
    """
    is_count = isinstance(changed_files, int) and not isinstance(changed_files, bool)
    if len(files) >= MAX_LISTED_FILES or (is_count and changed_files > len(files)):
        return "high"
    return pr_tier(changed_paths(files), risk_cfg)
