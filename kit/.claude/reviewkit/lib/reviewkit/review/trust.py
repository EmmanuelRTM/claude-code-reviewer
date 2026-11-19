"""Handle pull request content as data, never as configuration or code.

The review session runs in a checkout of the trusted base branch. The pull
request's tree is exported next to it as plain files: no symlinks, no git
filters, and nothing Claude Code would load as instructions (`CLAUDE.md`,
`.claude/`, `REVIEW.md`, MCP configuration). Those files remain visible to the
reviewer in the diff, where they are reviewed like any other change.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, List, Sequence

from ..gitio import git
from ..paths import match_any

PROTECTED_FILE_NAMES = frozenset(
    {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "REVIEW.md", ".mcp.json", ".claude.json"}
)
PROTECTED_DIRECTORY_NAMES = frozenset({".claude"})

# No symlinks, no smudge/LFS filters, no hooks: writing the tree runs nothing from the pull request.
_INERT_CHECKOUT = (
    "-c",
    "core.symlinks=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.process=",
    "-c",
    "filter.lfs.required=false",
)


def export_tree(repo: Path, commit: str, dest: Path) -> None:
    """Write the files of `commit` under `dest` without touching the repository's index or work tree."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"export destination is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    handle, index_name = tempfile.mkstemp(prefix="reviewkit-export-", dir=str(dest.parent))
    os.close(handle)
    os.unlink(index_name)
    env = {"GIT_INDEX_FILE": index_name, "GIT_LFS_SKIP_SMUDGE": "1"}
    try:
        git([*_INERT_CHECKOUT, "read-tree", commit], repo, env=env)
        git(
            [*_INERT_CHECKOUT, "checkout-index", "--all", "--force", f"--prefix={dest}{os.sep}"],
            repo,
            env=env,
            timeout=600,
        )
    finally:
        for leftover in (index_name, index_name + ".lock"):
            try:
                os.unlink(leftover)
            except OSError:
                pass


def sanitize_tree(root: Path) -> List[str]:
    """Remove configuration-like paths and symlinks at any depth; return what was removed."""
    root = Path(root)
    removed: List[str] = []
    for current, directories, files in os.walk(str(root), topdown=True, followlinks=False):
        here = Path(current)
        for name in list(directories):
            path = here / name
            if path.is_symlink():
                path.unlink()
            elif name in PROTECTED_DIRECTORY_NAMES:
                shutil.rmtree(str(path))
            else:
                continue
            directories.remove(name)
            removed.append(path.relative_to(root).as_posix())
        for name in files:
            path = here / name
            if path.is_symlink() or name in PROTECTED_FILE_NAMES:
                path.unlink()
                removed.append(path.relative_to(root).as_posix())
    return sorted(removed)


def protected_changes(changed_paths: Iterable[str], patterns: Sequence[str]) -> List[str]:
    """Changed paths that only a human code owner may approve (review rules, workflows, hooks)."""
    return sorted(path for path in set(changed_paths) if match_any(patterns, path))
