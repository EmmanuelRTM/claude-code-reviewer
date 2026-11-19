"""What a health check reports, and how a list of reports is shown."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Dict, Sequence

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
_SHOWN = {OK: "ok", WARN: "warn", FAIL: "FAIL", SKIP: "skip"}

INSTALL_FIX = "Re-run the kit installer against this repository: python3 install.py --target <path>"
ADMIN_ONLY = "needs repository admin access"


@dataclass(frozen=True)
class Check:
    """One observation. `fix` says what to do about a warning or a failure."""

    id: str
    status: str
    summary: str
    fix: str = ""


def tally(checks: Sequence[Check]) -> Dict[str, int]:
    return {status: sum(1 for check in checks if check.status == status) for status in (OK, WARN, FAIL, SKIP)}


def exit_code(checks: Sequence[Check], strict: bool = False) -> int:
    """1 when something is broken; with `strict`, also when something deserves attention."""
    counts = tally(checks)
    return 1 if counts[FAIL] or (strict and counts[WARN]) else 0


def render(checks: Sequence[Check]) -> str:
    width = max((len(check.id) for check in checks), default=0)
    lines = []
    for check in checks:
        lines.append(f"{_SHOWN[check.status]:<5} {check.id:<{width}}  {check.summary}")
        if check.fix and check.status in (WARN, FAIL):
            lines.append(f"{'':<5} {'':<{width}}  fix: {check.fix}")
    counts = tally(checks)
    lines += ["", f"{counts[OK]} ok, {counts[WARN]} warnings, {counts[FAIL]} failed, {counts[SKIP]} skipped"]
    return "\n".join(lines)


def to_json(checks: Sequence[Check], strict: bool = False) -> dict:
    return {
        "ok": exit_code(checks, strict) == 0,
        "counts": tally(checks),
        "checks": [dataclasses.asdict(check) for check in checks],
    }
