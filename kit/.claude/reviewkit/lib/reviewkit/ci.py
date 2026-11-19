"""GitHub Actions glue: step outputs, the job summary, annotations, the event.

Values handed to later steps always go through files (`GITHUB_OUTPUT`), never
through shell interpolation.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any, Mapping, Optional

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def set_output(name: str, value: Any, env: Optional[Mapping[str, str]] = None) -> None:
    if not _NAME.match(name):
        raise ValueError(f"invalid output name: {name!r}")
    path = _env(env).get("GITHUB_OUTPUT")
    if not path:
        return
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    with open(path, "a", encoding="utf-8") as handle:
        if "\n" in text or "\r" in text:
            delimiter = f"ghadelimiter_{secrets.token_hex(16)}"
            handle.write(f"{name}<<{delimiter}\n{text}\n{delimiter}\n")
        else:
            handle.write(f"{name}={text}\n")


def append_summary(markdown: str, env: Optional[Mapping[str, str]] = None) -> None:
    path = _env(env).get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip("\n") + "\n")


def load_event(env: Optional[Mapping[str, str]] = None) -> dict:
    path = _env(env).get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _annotate(level: str, message: str) -> None:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


def notice(message: str) -> None:
    _annotate("notice", message)


def warning(message: str) -> None:
    _annotate("warning", message)


def error(message: str) -> None:
    _annotate("error", message)


def run_url(env: Optional[Mapping[str, str]] = None) -> str:
    source = _env(env)
    server, repo, run_id = (source.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""
