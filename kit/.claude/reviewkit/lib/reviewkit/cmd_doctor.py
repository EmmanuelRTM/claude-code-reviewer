"""`doctor`: check that an installation is still wired the way it claims to be."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List

from .config import Config, ConfigError, load_config
from .doctor.local import local_checks
from .doctor.model import SKIP, Check, exit_code, render, to_json
from .doctor.remote import REMOTE_IDS, github_checks
from .github.client import client_from_env, discover_token, split_repo
from .gitio import GitError, git, repo_root

_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
_SCP_LIKE = re.compile(r"[^/@:]+@[^/:]+:(?P<path>.+)\Z")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/]+/(?P<path>.+)\Z")


def repo_from_remote(url: str) -> str:
    """`owner/name` from a remote URL, or "" when the remote is not hosted (a local path)."""
    match = _URL.match(url.strip()) or _SCP_LIKE.match(url.strip())
    if not match:
        return ""
    parts = [part for part in match.group("path").split("/") if part]
    if len(parts) < 2:
        return ""
    owner, name = parts[-2], parts[-1][:-4] if parts[-1].endswith(".git") else parts[-1]
    return f"{owner}/{name}" if _SEGMENT.match(owner) and _SEGMENT.match(name) else ""


def _repository_name(args, project: Path) -> str:
    if args.repo:
        return args.repo
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    try:
        return repo_from_remote(git(["remote", "get-url", "origin"], project))
    except GitError:
        return ""


def _skipped(summary: str) -> List[Check]:
    return [Check(check_id, SKIP, summary) for check_id in REMOTE_IDS]


def _github(args, project: Path) -> List[Check]:
    if not discover_token(os.environ):
        return _skipped("no GitHub token: set GH_TOKEN or sign in with `gh auth login`")
    try:
        owner, repo = split_repo(_repository_name(args, project))
    except ValueError:
        return _skipped("cannot tell which GitHub repository this is: pass --repo owner/name")
    try:
        cfg = load_config(project).config
    except ConfigError:
        cfg = Config()
    return github_checks(client_from_env(os.environ), owner, repo, cfg)


def _doctor(args) -> int:
    start = Path(args.project or ".").resolve()
    project = (repo_root(start) if start.is_dir() else None) or start
    checks = local_checks(project, os.environ)
    if args.github:
        checks += _github(args, project)
    print(json.dumps(to_json(checks, args.strict), indent=2) if args.json else render(checks))
    return exit_code(checks, args.strict)


def register(subparsers) -> None:
    parser = subparsers.add_parser("doctor", help="check that the review kit is installed and wired correctly")
    parser.add_argument("--project", help="repository to check (default: the current one)")
    parser.add_argument("--github", action="store_true", help="also read the repository's settings on GitHub")
    parser.add_argument("--repo", help="owner/name on GitHub (default: GITHUB_REPOSITORY, then the origin remote)")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.set_defaults(handler=_doctor)
