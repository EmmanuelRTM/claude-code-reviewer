"""Run the project's own checks (lint, build, test) and judge the outcome.

Only a real pass is green: a timeout, a non-zero exit, or a test run that
executed zero tests is reported as a failure, never worked around.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .config import Config

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Runner messages that mean "nothing was executed" even though the exit code is 0.
_ZERO_RUN_PATTERNS = (
    re.compile(r"\bno tests ran\b", re.I),  # pytest
    re.compile(r"\bcollected 0 items\b", re.I),  # pytest
    re.compile(r"\bNo tests found\b", re.I),  # jest
    re.compile(r"\bNo test files found\b", re.I),  # vitest
    re.compile(r"\bRan 0 tests\b"),  # unittest
    re.compile(r"\b0 examples, 0 failures\b"),  # rspec
    re.compile(r"^\s*0 passing\b", re.M),  # mocha
)
_CARGO_RUNNING = re.compile(r"^running (\d+) tests?$", re.M)
_GO_NO_TESTS = re.compile(r"\[no test files\]")
_GO_RAN = re.compile(r"^(ok|FAIL|---)\s", re.M)


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: str
    status: str  # pass | fail | timeout | zero_run | error
    exit_code: Optional[int]
    duration: float
    tail: str

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def tail_of(output: str, max_lines: int = 30, max_chars: int = 3000) -> str:
    """Last lines of `output`, without terminal escape codes, bounded in size."""
    lines = _ANSI.sub("", output).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n")
    return "\n".join(lines[-max_lines:])[-max_chars:]


def detect_zero_run(output: str) -> bool:
    text = _ANSI.sub("", output)
    if any(pattern.search(text) for pattern in _ZERO_RUN_PATTERNS):
        return True
    cargo = [int(n) for n in _CARGO_RUNNING.findall(text)]
    if cargo and sum(cargo) == 0:
        return True
    return bool(_GO_NO_TESTS.search(text)) and not _GO_RAN.search(text)


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (OSError, ProcessLookupError):
        pass


def run_check(
    name: str,
    command: str,
    cwd: Path,
    timeout: float,
    zero_run_is_failure: bool = True,
) -> CheckResult:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S602 - the command is the project's own committed check
            command,
            shell=True,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return CheckResult(name, command, "error", None, time.monotonic() - started, str(exc))
    try:
        raw, _ = proc.communicate(timeout=max(timeout, 0.01))
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raw, _ = proc.communicate()
        timed_out = True
    output = (raw or b"").decode("utf-8", "replace")
    duration = time.monotonic() - started
    if timed_out:
        status = "timeout"
    elif proc.returncode != 0:
        status = "fail"
    elif name == "test" and zero_run_is_failure and detect_zero_run(output):
        status = "zero_run"
    else:
        status = "pass"
    return CheckResult(name, command, status, None if timed_out else proc.returncode, duration, tail_of(output))


def configured_checks(cfg: Config, names: Optional[Sequence[str]] = None) -> list:
    """`(name, command)` pairs to run, in configured order, skipping unset commands."""
    wanted = list(names) if names else list(cfg.checks.on_stop)
    return [(name, getattr(cfg.commands, name)) for name in wanted if getattr(cfg.commands, name, "")]


def run_checks(cfg: Config, cwd: Path, names: Optional[Sequence[str]] = None) -> list:
    """Run each configured check until one fails; all share one time budget."""
    deadline = time.monotonic() + cfg.checks.timeout_seconds
    results: list = []
    for name, command in configured_checks(cfg, names):
        result = run_check(name, command, cwd, deadline - time.monotonic(), cfg.checks.zero_run_is_failure)
        results.append(result)
        if not result.ok:
            break
    return results


_STATUS_TEXT = {
    "timeout": "timed out",
    "zero_run": "ran zero tests, which is not a pass",
    "error": "could not start",
}


def format_failure(result: CheckResult) -> str:
    outcome = _STATUS_TEXT.get(result.status) or f"failed with exit code {result.exit_code}"
    return f"`{result.name}` check {outcome}: {result.command}\n--- last output ---\n{result.tail}"
