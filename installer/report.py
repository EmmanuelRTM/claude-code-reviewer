"""What the installer did (or would do), and how it is printed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
MERGED = "merged"
SKIPPED = "skipped (locally modified)"
REMOVED = "removed"
KEPT = "kept (user-owned)"
ERROR = "error"

_CHANGES = frozenset({CREATED, UPDATED, MERGED, REMOVED})


@dataclass(frozen=True)
class Entry:
    """The outcome for one target-relative path."""

    path: str
    status: str
    detail: str = ""


@dataclass
class Report:
    """Everything one run decided, in the order it was planned."""

    mode: str  # "install" or "uninstall"
    target: Path
    kit_version: str
    dry_run: bool
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Entry]:
        return [entry for entry in self.entries if entry.status == ERROR]

    @property
    def ok(self) -> bool:
        """True when every path could be handled."""
        return not self.errors

    @property
    def changed(self) -> bool:
        """True when at least one path was (or, in a dry run, would be) written or removed."""
        return any(entry.status in _CHANGES for entry in self.entries)

    def status_of(self, path: str) -> Optional[str]:
        for entry in self.entries:
            if entry.path == path:
                return entry.status
        return None

    def paths(self, status: str) -> list[str]:
        return [entry.path for entry in self.entries if entry.status == status]


def next_steps() -> list[str]:
    """What remains to be done by hand after a successful install."""
    return [
        "Review and commit the changes on a branch, open a pull request and merge it into the default "
        "branch (the review workflows run from the default branch).",
        "Add the repository secret CLAUDE_CODE_OAUTH_TOKEN (generate it locally with `claude setup-token`); "
        "never commit it.",
        "Install the Claude GitHub App (https://github.com/apps/claude) if you want @claude to work on "
        "issues and pull requests.",
        "Protect the default branch: require a pull request, review from code owners and the "
        "`risk-approval` status check. Check the `owners` setting.",
        "Run `python3 -I .claude/reviewkit/rk.py doctor --github` in the target repository to confirm the "
        "installation, the secret and the branch protection.",
    ]


def _describe(entry: Entry) -> str:
    if entry.status == ERROR:
        return f"{entry.path}: {entry.detail}" if entry.detail else entry.path
    return f"{entry.path} ({entry.detail})" if entry.detail else entry.path


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def format_report(report: Report) -> str:
    """Render the report: a header, one line per path, then notes and next steps."""
    installing = report.mode == "install"
    action = "install into" if installing else "uninstall from"
    lines = [f"reviewkit {report.kit_version}: {action} {report.target}"]
    if report.dry_run:
        lines.append("Dry run: nothing was written. This is what would happen.")
    lines.append("")
    width = max((len(entry.status) for entry in report.entries), default=0)
    lines.extend(f"  {entry.status.ljust(width)}  {_describe(entry)}" for entry in report.entries)
    if not report.entries:
        lines.append("  nothing to do")

    if report.warnings:
        lines += ["", "Warnings:"]
        lines.extend(f"  - {warning}" for warning in report.warnings)

    skipped = len(report.paths(SKIPPED))
    if skipped:
        outcome = "replaces such files with the kit's version" if installing else "removes such files too"
        lines += ["", f"Left alone because of local modifications: {_plural(skipped, 'file')}. --force {outcome}."]
    if not installing and report.paths(KEPT):
        lines += ["", "Files marked kept (user-owned) were left in place. Delete them if you no longer want them."]

    if report.errors:
        failed = _plural(len(report.errors), "path")
        lines += ["", f"{failed} could not be handled. Fix the problems above and run the installer again."]
    elif installing and not report.dry_run:
        lines += ["", "Next steps:"]
        lines.extend(f"  {number}. {step}" for number, step in enumerate(next_steps(), start=1))
    return "\n".join(lines) + "\n"
