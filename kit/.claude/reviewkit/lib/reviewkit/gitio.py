"""Thin wrapper around the `git` executable.

Every call names its repository explicitly and never inherits a pager, an
editor prompt, or attribute-driven external programs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Optional, Sequence


class GitError(Exception):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {stderr.strip()[:500]}")
        self.returncode = returncode
        self.stderr = stderr


# Applied to every invocation: no prompts, no pager, no hooks from the data we read.
_SAFE_CONFIG = ("-c", "core.pager=cat", "-c", "core.fsmonitor=false")


def git(
    args: Sequence[str],
    cwd: Path,
    *,
    env: Optional[Mapping[str, str]] = None,
    check: bool = True,
    stdin: Optional[bytes] = None,
    timeout: float = 120,
    raw: bool = False,
):
    """Run git in `cwd` and return stdout (text, or bytes when `raw`)."""
    full_env = dict(os.environ)
    full_env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            ["git", *_SAFE_CONFIG, *args],
            cwd=str(cwd),
            env=full_env,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(args, -1, str(exc)) from exc
    if check and proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc.stdout if raw else proc.stdout.decode("utf-8", "replace")


def repo_root(cwd: Path) -> Optional[Path]:
    """Top-level directory of the work tree containing `cwd`, or None."""
    try:
        out = git(["rev-parse", "--show-toplevel"], cwd).strip()
    except GitError:
        return None
    return Path(out) if out else None


def git_path(cwd: Path, name: str) -> Path:
    """Resolve a path inside the git directory (worktree-aware)."""
    out = git(["rev-parse", "--git-path", name], cwd).strip()
    path = Path(out)
    return path if path.is_absolute() else Path(cwd) / path


def head_sha(cwd: Path) -> Optional[str]:
    try:
        return git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd).strip() or None
    except GitError:
        return None
