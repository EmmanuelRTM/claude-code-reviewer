"""A plan: plain data describing what one run will do to the target.

Planning only reads. `operations.apply_plan` is the single place that writes,
which is what makes `--dry-run` trustworthy: it prints the same plan and stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from installer.report import ERROR, REMOVED

WRITE = "write"
DELETE = "delete"


@dataclass(frozen=True)
class Action:
    """The outcome for one path and, when the disk must change, how."""

    path: str  # target-relative POSIX path
    status: str  # what the report says
    detail: str = ""
    operation: Optional[str] = None  # WRITE, DELETE, or None to leave the disk alone
    content: Optional[bytes] = None  # WRITE only
    executable: Optional[bool] = None  # WRITE only: set or clear the bit; None keeps the current mode

    @classmethod
    def write(
        cls, path: str, status: str, content: bytes, *, executable: Optional[bool] = None, detail: str = ""
    ) -> Action:
        return cls(path, status, detail, WRITE, content, executable)

    @classmethod
    def delete(cls, path: str, detail: str = "") -> Action:
        return cls(path, REMOVED, detail, DELETE)

    @classmethod
    def note(cls, path: str, status: str, detail: str = "") -> Action:
        """A path that is reported but not touched."""
        return cls(path, status, detail)

    @classmethod
    def error(cls, path: str, reason: str) -> Action:
        """A path that could not be handled; it is left exactly as it is."""
        return cls(path, ERROR, reason)


@dataclass
class Plan:
    """Everything one run intends to do, in order. The manifest comes last."""

    mode: str  # "install" or "uninstall"
    kit_version: str
    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
