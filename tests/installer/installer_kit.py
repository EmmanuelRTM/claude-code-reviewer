"""Test helpers for the installer: a small throwaway kit and tree snapshots.

The tests install this miniature payload instead of the real `kit/`, so they
stay valid while the real payload changes. Every class of file is represented.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from installer.operations import install, uninstall
from installer.report import Report

FRAGMENT = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 -I .claude/hooks/gate.py"}]}
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "python3 -I .claude/hooks/stop.py", "timeout": 900}]}],
    }
}

CLAUDE_BLOCK = """<!-- reviewkit:begin -->
## Verification contract

- Test: {{TEST_COMMAND}}
- Lint: {{LINT_COMMAND}}
- Build: {{BUILD_COMMAND}}
<!-- reviewkit:end -->
"""

CODEOWNERS_BLOCK = """# reviewkit:begin
# Owned by people.
{{CODEOWNERS_RULES}}
# reviewkit:end
"""

GITIGNORE_BLOCK = """# reviewkit:begin
.claude/state/
.claude/settings.local.json
# reviewkit:end
"""

KIT_FILES = {
    ".claude/reviewkit/rk.py": "#!/usr/bin/env python3\nprint('rk')\n",
    ".claude/reviewkit/lib/reviewkit/__init__.py": '"""Fixture library."""\n\n__version__ = "9.9.9"\n',
    ".claude/reviewkit/lib/reviewkit/config.py": "VALUE = 1\n",
    ".claude/hooks/gate.py": "print('gate')\n",
    ".claude/hooks/stop.py": "print('stop')\n",
    ".claude/skills/pr-review/SKILL.md": "# pr-review\n",
    ".claude/skills/bug-finding/SKILL.md": "# bug-finding\n",
    ".claude/skills/finding-verification/SKILL.md": "# finding-verification\n",
    ".claude/agents/bug-finder.md": "# bug-finder\n",
    ".claude/agents/finding-verifier.md": "# finding-verifier\n",
    ".github/workflows/claude-review.yml": "name: review\n",
    ".github/ISSUE_TEMPLATE/bug.yml": "name: bug\n",
}

USER_FILES = {
    "REVIEW.md": "# Review rules\n",
    "docs/reliability-checklist.md": "# Checklist\n",
    ".claude/skills/verify/SKILL.md": "# verify\n",
    ".github/pull_request_template.md": "## Summary\n",
    ".github/dependabot.yml": "version: 2\n",
}

EXECUTABLE_FILES = (".claude/reviewkit/rk.py",)


class KitSource:
    """A miniature kit on disk: a payload tree plus block templates."""

    def __init__(self, root: Path) -> None:
        self.payload = root / "payload"
        self.blocks = root / "blocks"

    def write(self, relative: str, content: str = "", *, executable: bool = False) -> Path:
        path = self.payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def remove(self, relative: str) -> None:
        (self.payload / relative).unlink()

    def write_block(self, name: str, content: str) -> None:
        self.blocks.mkdir(parents=True, exist_ok=True)
        (self.blocks / name).write_text(content, encoding="utf-8")

    def install(self, repo: Any, **options: Any) -> Report:
        return install(Path(repo.path), payload_root=self.payload, blocks_root=self.blocks, **options)

    def uninstall(self, repo: Any, **options: Any) -> Report:
        return uninstall(Path(repo.path), payload_root=self.payload, **options)


def build_kit(root: Path) -> KitSource:
    """A complete miniature kit: kit-owned, user-owned, special and junk files."""
    source = KitSource(root)
    for relative, content in {**KIT_FILES, **USER_FILES}.items():
        source.write(relative, content, executable=relative in EXECUTABLE_FILES)
    source.write(".claude/settings.json", json.dumps(FRAGMENT, indent=2) + "\n")
    # Never copied: the installer generates the configuration itself.
    source.write(".claude/reviewkit.json", '{"gate": {"mode": "enforce"}}\n')
    # Junk that must be skipped.
    source.write(".claude/reviewkit/lib/reviewkit/__pycache__/config.cpython-313.pyc", "junk")
    source.write(".claude/hooks/stray.pyc", "junk")
    source.write_block("CLAUDE.md.block", CLAUDE_BLOCK)
    source.write_block("CODEOWNERS.block", CODEOWNERS_BLOCK)
    source.write_block("gitignore.block", GITIGNORE_BLOCK)
    return source


def snapshot(root: Path) -> dict:
    """Everything under `root`: kinds, bytes, permission bits and modification times."""
    result: dict = {}
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ("dir",)
            else:
                result[relative] = ("file", path.read_bytes(), stat.S_IMODE(info.st_mode), info.st_mtime_ns)
    return result


def files_only(root: Path, *, skip_git: bool = True) -> dict:
    """Map of relative path to bytes for every regular file (ignoring `.git` by default)."""
    result: dict = {}
    for relative, entry in snapshot(root).items():
        if skip_git and (relative == ".git" or relative.startswith(".git/")):
            continue
        if entry[0] == "file":
            result[relative] = entry[1]
    return result
