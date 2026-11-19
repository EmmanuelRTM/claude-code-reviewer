"""Which paths a CODEOWNERS file assigns to someone.

Follows GitHub's rules closely enough for a health check: the last matching
pattern wins, a pattern without owners un-assigns, a pattern without a slash
matches at any depth, and a leading slash anchors to the repository root.
"""

from __future__ import annotations

from typing import List, Mapping

from ..paths import glob_match


def pattern_matches(pattern: str, path: str) -> bool:
    if pattern in ("*", "**", "/"):
        return True
    core = pattern.strip("/")
    if not core:
        return False
    parts = path.split("/")
    if pattern.startswith("/") or "/" in pattern.rstrip("/"):
        # Anchored to the root. A final segment with a wildcard (`docs/*`) names entries of one
        # directory only; a plain name (`/docs`) also covers everything below it.
        covers_below = pattern.endswith("/") or "*" not in core.rsplit("/", 1)[-1]
        return glob_match(core, path) or (covers_below and glob_match(core + "/**", path))
    # Not anchored: the pattern may name the file itself or any directory above it, at any depth.
    spans = ["/".join(parts[i:j]) for i in range(len(parts)) for j in range(i + 1, len(parts) + 1)]
    return any(glob_match(core, span) for span in spans)


def owners_of(codeowners: str, path: str) -> List[str]:
    owners: List[str] = []
    for line in codeowners.splitlines():
        fields = line.split("#", 1)[0].split()
        if fields and pattern_matches(fields[0], path):
            owners = fields[1:]
    return owners


def uncovered(codeowners: str, samples: Mapping[str, str]) -> List[str]:
    """Labels from `samples` (label -> example path) that no owner is assigned to."""
    return [label for label, path in samples.items() if not owners_of(codeowners, path)]
