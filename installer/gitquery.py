"""The installer's only subprocess: read-only `git` queries against the target.

Two questions are asked, `rev-parse --show-toplevel` and `symbolic-ref` on the
remote HEAD. Neither runs hooks, touches the index or the working tree, or
needs the network. Every call has a timeout, and git variables inherited from
the caller that would point the query at another repository are dropped.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from installer.errors import InstallError

TIMEOUT_SECONDS = 10

# Set by git itself when it runs a hook or an alias; they would redirect `git -C`.
_REDIRECTING_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
    }
)
# Conservative on purpose: the name is written into a configuration that scripts read.
_BRANCH_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_REMOTE_PREFIXES = ("remotes/origin/", "origin/")


def _git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = {name: value for name, value in os.environ.items() if name not in _REDIRECTING_VARIABLES}
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        return subprocess.run(
            ["git", "-C", str(directory), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise InstallError("git was not found on PATH; it is needed to check the target repository") from exc
    except subprocess.TimeoutExpired as exc:
        raise InstallError(f"git did not answer within {TIMEOUT_SECONDS} seconds for {directory}") from exc
    except OSError as exc:
        raise InstallError(f"git could not be run: {exc}") from exc


def work_tree_root(directory: Path) -> Path:
    """Top level of the git work tree that contains `directory`, or raise `InstallError`."""
    result = _git(directory, "rev-parse", "--show-toplevel")
    output = os.fsdecode(result.stdout)
    top = output[:-1] if output.endswith("\n") else output
    if result.returncode != 0 or not top:
        lines = os.fsdecode(result.stderr).strip().splitlines()
        reason = f" ({lines[-1]})" if lines else ""
        raise InstallError(f"{directory} is not inside a git work tree{reason}")
    return Path(top)


def origin_default_branch(directory: Path) -> Optional[str]:
    """The branch `origin/HEAD` points at, or None when unknown.

    Best effort: a repository without a remote, or one whose branch name has
    characters outside a conservative set, simply yields None.
    """
    try:
        result = _git(directory, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except InstallError:
        return None
    if result.returncode != 0:
        return None
    name = os.fsdecode(result.stdout).strip()
    for prefix in _REMOTE_PREFIXES:
        if name.startswith(prefix):
            branch = name[len(prefix) :]
            return branch if _BRANCH_NAME.match(branch) else None
    return None
