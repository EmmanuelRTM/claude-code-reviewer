"""Unified diff parsing.

GitHub accepts an inline review comment only on a line that is part of the
pull request's diff. This module turns a `git diff` into, per file, the set of
new-file ("right side") line numbers that qualify, so every finding can be
routed deterministically: inline when its line is in the diff, otherwise into
the summary table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..gitio import git

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_OCTAL = re.compile(r"\\([0-7]{3})")
_SIMPLE_ESCAPES = {"\\\\": "\\", '\\"': '"', "\\t": "\t", "\\n": "\n"}

DIFF_OPTIONS = (
    "-c",
    "core.quotePath=false",
    "diff",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "-M",
    "--unified=3",
)


@dataclass
class FileDiff:
    path: str
    old_path: str
    status: str = "modified"  # added | modified | deleted | renamed
    binary: bool = False
    additions: int = 0
    deletions: int = 0
    right_lines: frozenset = frozenset()
    added_lines: frozenset = frozenset()
    hunks: Tuple[Tuple[int, int], ...] = ()  # (first, last) new-file line of each hunk
    span: Tuple[int, int] = (0, 0)  # [start, end) line indexes of this file's section in the diff text
    _right: List[int] = field(default_factory=list, repr=False)
    _added: List[int] = field(default_factory=list, repr=False)

    def commentable(self, line: int) -> bool:
        return line in self.right_lines

    def range_commentable(self, start: int, end: int) -> bool:
        """True when `start..end` lies inside a single hunk."""
        return start <= end and any(first <= start and end <= last for first, last in self.hunks)


def _unquote(token: str) -> str:
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
        raw = _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), token)
        for escaped, plain in _SIMPLE_ESCAPES.items():
            raw = raw.replace(escaped, plain)
        try:
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return raw
    return token


def _strip_prefix(token: str, prefix: str) -> str:
    token = _unquote(token.rstrip("\t\r"))
    return token[len(prefix) :] if token.startswith(prefix) else token


def _header_paths(line: str) -> Tuple[str, str]:
    """Best-effort `(old, new)` from a `diff --git` line (refined by later headers)."""
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        end = rest.find('" ', 1)
        first, second = rest[: end + 1], rest[end + 2 :]
    else:
        middle = rest.find(" b/")
        first, second = (rest[:middle], rest[middle + 1 :]) if middle != -1 else (rest, rest)
    return _strip_prefix(first, "a/"), _strip_prefix(second, "b/")


def parse_unified_diff(text: str) -> Dict[str, FileDiff]:
    """Parse `git diff` output into `{new_path: FileDiff}` in diff order."""
    files: Dict[str, FileDiff] = {}
    current: Optional[FileDiff] = None
    in_hunk = False
    right = remaining_old = remaining_new = 0
    hunk_first = 0
    hunks: List[Tuple[int, int]] = []

    def close_hunk() -> None:
        nonlocal in_hunk
        if in_hunk and current is not None and right > hunk_first:
            hunks.append((hunk_first, right - 1))
        in_hunk = False

    def finish(end: int) -> None:
        close_hunk()
        if current is not None:
            current.right_lines = frozenset(current._right)
            current.added_lines = frozenset(current._added)
            current.hunks = tuple(hunks)
            current.span = (section_start, end)
            files[current.path] = current

    section_start = 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if in_hunk and (remaining_old > 0 or remaining_new > 0) and current is not None:
            marker = line[:1]
            if marker == "+":
                current._right.append(right)
                current._added.append(right)
                current.additions += 1
                right += 1
                remaining_new -= 1
                continue
            if marker == "-":
                current.deletions += 1
                remaining_old -= 1
                continue
            if marker == "\\":
                continue
            current._right.append(right)  # context line (a blank context line may lose its space)
            right += 1
            remaining_old -= 1
            remaining_new -= 1
            continue
        if line.startswith("diff --git "):
            finish(index)
            section_start = index
            old_path, new_path = _header_paths(line)
            current = FileDiff(path=new_path, old_path=old_path)
            hunks = []
            continue
        if current is None:
            continue
        if line.startswith("@@ "):
            close_hunk()
            match = _HUNK.match(line)
            if match:
                remaining_old = int(match.group(2) or "1")
                right = hunk_first = int(match.group(3))
                remaining_new = int(match.group(4) or "1")
                in_hunk = True
        elif line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith("rename from "):
            current.status = "renamed"
            current.old_path = _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            current.status = "renamed"
            current.path = _unquote(line[len("rename to ") :])
        elif line.startswith("--- ") and not line.startswith("--- /dev/null"):
            current.old_path = _strip_prefix(line[4:], "a/")
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            current.path = _strip_prefix(line[4:], "b/")
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.binary = True
    finish(len(lines))
    return files


def truncate_diff(text: str, files: Dict[str, FileDiff], max_file_lines: int) -> Tuple[str, List[str]]:
    """Replace the body of oversized file sections with a one-line note; return `(text, truncated paths)`."""
    lines = text.split("\n")
    truncated: List[str] = []
    for entry in sorted(files.values(), key=lambda item: item.span[0], reverse=True):
        if entry.additions + entry.deletions <= max_file_lines:
            continue
        start, end = entry.span
        header_end = next((i for i in range(start, end) if lines[i].startswith("@@ ")), end)
        note = (
            f"[diff body omitted: {entry.additions} added and {entry.deletions} removed lines. "
            "Read the file in the head tree instead.]"
        )
        lines[header_end:end] = [note]
        truncated.append(entry.path)
    return "\n".join(lines), sorted(truncated)


def diff_stats(files: Dict[str, FileDiff], text: str) -> dict:
    return {
        "files": len(files),
        "additions": sum(entry.additions for entry in files.values()),
        "deletions": sum(entry.deletions for entry in files.values()),
        "diff_bytes": len(text.encode("utf-8")),
    }


def unified_diff(repo: Path, base: str, head: str) -> str:
    """The pull request diff, with no attribute-driven external programs involved."""
    return git([*DIFF_OPTIONS, base, head, "--"], repo, timeout=300)


def patch_id(repo: Path, base: str, head: str) -> str:
    """Stable id of the change `base..head`; unchanged by a clean rebase or merge of the base."""
    raw = git([*DIFF_OPTIONS, base, head, "--"], repo, timeout=300, raw=True)
    if not raw.strip():
        return ""
    out = git(["patch-id", "--stable"], repo, stdin=raw, timeout=120)
    return out.split()[0] if out.split() else ""
