"""Claude Code hook I/O: read the event from stdin, answer with exit code and JSON.

Hooks fail open: an internal error is reported on stderr and the action goes
ahead, so a broken check never traps a session. A block is always an explicit
decision (exit code 2 with a reason on stderr).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from ..gitio import repo_root

BLOCK = 2


@dataclass(frozen=True)
class HookResult:
    exit_code: int = 0
    stdout: Optional[dict] = None
    stderr: str = ""


ALLOW = HookResult()


def read_event(stream=None) -> Optional[dict]:
    try:
        data = json.loads((stream or sys.stdin).read() or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def project_dir_for(event: Mapping, env: Mapping[str, str]) -> Path:
    """The work tree Claude is acting in.

    `cwd` follows Claude into a linked worktree while `CLAUDE_PROJECT_DIR` stays
    at the session root, so the event's `cwd` wins. Only the first location that
    is known is considered: a hook never falls through to some other repository.
    """
    location = Path(event.get("cwd") or env.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    root = repo_root(location) if location.is_dir() else None
    return root if root is not None else location


def pre_tool_context(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def system_message(text: str) -> dict:
    return {"systemMessage": text}


def run(evaluate: Callable[[dict, Mapping[str, str]], HookResult], env: Optional[Mapping[str, str]] = None) -> int:
    """Read the event, call `evaluate`, emit its result. Never raises."""
    event = read_event()
    if event is None:
        return 0
    try:
        result = evaluate(event, os.environ if env is None else env)
    except Exception as exc:  # noqa: BLE001 - fail open, but say why
        print(f"reviewkit hook error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    if result.stdout:
        sys.stdout.write(json.dumps(result.stdout))
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.exit_code
