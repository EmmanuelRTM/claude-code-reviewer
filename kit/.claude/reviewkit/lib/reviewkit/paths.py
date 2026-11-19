"""Repository-relative path handling and glob matching.

Globs are anchored at the repository root and use `/` as the separator:

* `*` matches any run of characters except `/`
* `?` matches one character except `/`
* `**/` matches zero or more directories, `/**` matches everything below
* a trailing `/` is shorthand for `/**`

A bare name such as `REVIEW.md` therefore matches only the root file; write
`**/REVIEW.md` to match at any depth.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

STATE_DIR_RELATIVE = ".claude/state"


def normalize_path(raw: str) -> str:
    """Return a clean repository-relative POSIX path, or raise ValueError."""
    parts: list[str] = []
    for part in raw.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"path escapes the repository: {raw!r}")
        parts.append(part)
    if not parts:
        raise ValueError("empty path")
    return "/".join(parts)


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    if pattern.endswith("/"):
        pattern += "**"
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    return glob_to_regex(pattern).match(path) is not None


def match_any(patterns: Iterable[str], path: str) -> bool:
    return any(glob_match(pattern, path) for pattern in patterns)


def state_dir(project_dir: Path) -> Path:
    """Local, gitignored state: verification markers, counters and the gate log."""
    return project_dir / STATE_DIR_RELATIVE
