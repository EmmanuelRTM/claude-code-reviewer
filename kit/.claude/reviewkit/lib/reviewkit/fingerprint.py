"""Working-tree fingerprint: one hash for the exact content you would commit.

The fingerprint is the git tree id of the work tree (tracked changes plus
untracked, non-ignored files). It is computed through a temporary index, so the
real index and the work tree are never touched. Because it identifies content
rather than history, committing or amending without changing content keeps it,
and any edit changes it. That is what lets "verified" mean "this exact tree".
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .gitio import GitError, git, git_path, repo_root
from .paths import STATE_DIR_RELATIVE


class FingerprintError(Exception):
    """The work tree could not be fingerprinted (not a repository, git failure)."""


def tree_fingerprint(project_dir: Path) -> str:
    root = repo_root(Path(project_dir))
    if root is None:
        raise FingerprintError(f"not inside a git work tree: {project_dir}")
    try:
        index = git_path(root, "index")
        index.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix="reviewkit-index-", dir=str(index.parent))
        os.close(handle)
        temp_index = Path(temp_name)
        try:
            if index.is_file():
                shutil.copyfile(str(index), temp_name)
            else:
                temp_index.unlink()  # git must create it: an empty file is not a valid index
            env = {"GIT_INDEX_FILE": temp_name}
            git(["add", "-A", "--", "."], root, env=env)
            # Drop the local state directory even when a repository forgot to ignore it, so
            # writing the verification marker can never change the fingerprint. (An exclude
            # pathspec cannot be used: git rejects one that names an already-ignored path.)
            git(["rm", "-r", "-q", "-f", "--cached", "--ignore-unmatch", "--", STATE_DIR_RELATIVE], root, env=env)
            tree = git(["write-tree"], root, env=env).strip()
        finally:
            for leftover in (temp_index, Path(temp_name + ".lock")):
                try:
                    leftover.unlink()
                except OSError:
                    pass
    except (GitError, OSError) as exc:
        raise FingerprintError(str(exc)) from exc
    if not tree:
        raise FingerprintError("git write-tree returned nothing")
    return tree


def is_clean(project_dir: Path) -> bool:
    """True when there is nothing uncommitted (no tracked changes, no untracked files)."""
    try:
        return git(["status", "--porcelain", "--untracked-files=normal"], Path(project_dir)).strip() == ""
    except GitError as exc:
        raise FingerprintError(str(exc)) from exc


def changed_paths(project_dir: Path) -> list:
    """Repository-relative paths with uncommitted changes (staged, unstaged, untracked)."""
    try:
        raw = git(["status", "--porcelain", "-z", "--untracked-files=all"], Path(project_dir))
    except GitError as exc:
        raise FingerprintError(str(exc)) from exc
    paths, entries, i = [], raw.split("\0"), 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) > 3:
            paths.append(entry[3:])
            if entry[0] in "RC" or entry[1] in "RC":  # rename/copy: the next field is the old path
                i += 1
                if i < len(entries) and entries[i]:
                    paths.append(entries[i])
        i += 1
    return paths
