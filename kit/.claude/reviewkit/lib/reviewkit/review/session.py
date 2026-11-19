"""The review session's command line: read-only tools, no hooks, no reads outside the two trees.

Used by the workflow (through `review prepare`) and by the local eval runner,
so both drive Claude Code with exactly the same restrictions.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from ..config import Config
from .context import Workspace
from .schema import schema_json

READ_ONLY_TOOLS = "Read,Glob,Grep,Agent"
DENIED_TOOLS = "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch"


def claude_args(cfg: Config, workspace: Workspace) -> str:
    """Arguments for the review session, as one shell-quoted string."""
    settings = {
        "disableAllHooks": True,
        "permissions": {
            "additionalDirectories": [str(workspace.root)],
            "blockReadsOutsideWorkingDirectories": True,
        },
        "env": {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
    }
    values = [
        "--model", cfg.review.models.lead,
        "--effort", cfg.review.effort,
        "--max-turns", str(cfg.review.max_turns),
        "--permission-mode", "default",
        "--tools", READ_ONLY_TOOLS,
        "--disallowedTools", DENIED_TOOLS,
        "--settings", json.dumps(settings, separators=(",", ":")),
        "--json-schema", schema_json(),
    ]  # fmt: skip
    if any("'" in value for value in values):
        raise ValueError("a review argument contains a single quote and cannot be passed safely")
    return " ".join(shlex.quote(value) for value in values)


def run_claude(prompt: str, arguments: str, cwd: Path, timeout: float = 1800) -> Optional[dict]:
    """Run one non-interactive session locally and return its JSON result, or None on failure."""
    command = ["claude", "-p", prompt, *shlex.split(arguments), "--output-format", "json"]
    try:
        proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
