"""Lay the kit out in a test repository the way an installation leaves it."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from conftest import KIT_ROOT, GitRepo

CLAUDE_MD = """# Project

<!-- reviewkit:begin -->
## Verification contract

- Tests: `pytest -q`
<!-- reviewkit:end -->
"""

CODEOWNERS = """# reviewkit:begin
/.claude/ @octo/maintainers
/.github/workflows/ @octo/maintainers
/REVIEW.md @octo/maintainers
/docs/reliability-checklist.md @octo/maintainers
# reviewkit:end
"""


def install_payload(repo: GitRepo, config: dict = None, *, commit: bool = True, manifest: bool = True) -> None:
    """Copy the payload, write a configuration and the merged files, record a manifest."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(KIT_ROOT / ".claude", repo.path / ".claude", ignore=ignore)
    shutil.copytree(KIT_ROOT / ".github", repo.path / ".github", ignore=ignore)
    shutil.copy(KIT_ROOT / "REVIEW.md", repo.path / "REVIEW.md")
    (repo.path / "docs").mkdir(exist_ok=True)
    shutil.copy(KIT_ROOT / "docs" / "reliability-checklist.md", repo.path / "docs" / "reliability-checklist.md")
    write_config(repo, config if config is not None else {"version": 1, "commands": {"test": "pytest -q"}})
    repo.write(".gitignore", ".claude/state/\n.claude/settings.local.json\n")
    repo.write("CLAUDE.md", CLAUDE_MD)
    repo.write(".github/CODEOWNERS", CODEOWNERS)
    if manifest:
        write_manifest(repo)
    if commit:
        repo.commit("install review kit")


def write_config(repo: GitRepo, config: dict) -> None:
    repo.write(".claude/reviewkit.json", json.dumps(config, indent=2) + "\n")


def kit_owned_files(root: Path) -> list:
    owned = []
    for base in (".claude/reviewkit", ".claude/hooks", ".claude/agents", ".claude/skills/pr-review"):
        for path in sorted((root / base).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.name != "manifest.json":
                owned.append(path.relative_to(root).as_posix())
    return owned


def write_manifest(repo: GitRepo) -> dict:
    files = {
        relative: hashlib.sha256((repo.path / relative).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for relative in kit_owned_files(repo.path)
    }
    document = {"kit_version": "0.1.0", "files": files}
    repo.write(".claude/reviewkit/manifest.json", json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def by_id(checks) -> dict:
    """Checks keyed by id; a repeated id keeps the most severe one."""
    order = {"ok": 0, "skip": 1, "warn": 2, "fail": 3}
    found: dict = {}
    for check in checks:
        if check.id not in found or order[check.status] > order[found[check.id].status]:
            found[check.id] = check
    return found
