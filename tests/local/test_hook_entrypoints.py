"""The hook scripts as Claude Code runs them: a subprocess fed JSON on stdin."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import KIT_ROOT

HOOKS = KIT_ROOT / ".claude" / "hooks"
PY = sys.executable


def run_hook(script, event, cwd, env_extra=None):
    import os

    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "CI")}
    env.update(env_extra or {})
    return subprocess.run(
        [PY, "-I", str(HOOKS / script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=60,
        check=False,
    )


def commit_event(repo):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m 'feat: x' -m 'Verified-By: verify'"},
        "session_id": "sub",
        "cwd": str(repo.path),
    }


def test_commit_gate_observe_prints_one_json_object_and_exits_zero(repo):
    repo.write("app.py", "x\n")
    proc = run_hook("verify_commit_gate.py", commit_event(repo), repo.path)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)  # exactly one JSON object, nothing else on stdout
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_commit_gate_enforce_exits_two_with_the_reason_on_stderr(repo):
    repo.write(".claude/reviewkit.json", json.dumps({"gate": {"mode": "enforce"}}))
    repo.write("app.py", "x\n")
    proc = run_hook("verify_commit_gate.py", commit_event(repo), repo.path)
    assert proc.returncode == 2
    assert proc.stdout == "" and "Commit blocked" in proc.stderr


def test_commit_gate_ignores_other_commands_quietly(repo):
    event = commit_event(repo)
    event["tool_input"]["command"] = "git status"
    proc = run_hook("verify_commit_gate.py", event, repo.path)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def test_stop_hook_blocks_on_a_failing_check(repo):
    failing = f"{PY} -c \"import sys; print('nope'); sys.exit(1)\""
    repo.write(".claude/reviewkit.json", json.dumps({"commands": {"test": failing}}))
    repo.write("app.py", "x\n")
    event = {"hook_event_name": "Stop", "session_id": "sub", "cwd": str(repo.path), "permission_mode": "default"}
    proc = run_hook("run_checks_on_stop.py", event, repo.path)
    assert proc.returncode == 2 and "nope" in proc.stderr and proc.stdout == ""


@pytest.mark.parametrize("script", ["verify_commit_gate.py", "run_checks_on_stop.py"])
def test_garbage_input_never_blocks(repo, script):
    proc = subprocess.run(
        [PY, "-I", str(HOOKS / script)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=str(repo.path),
        check=False,
    )
    assert proc.returncode == 0


@pytest.mark.parametrize("script", ["verify_commit_gate.py", "run_checks_on_stop.py"])
def test_hooks_do_nothing_in_ci(repo, script):
    repo.write(".claude/reviewkit.json", json.dumps({"gate": {"mode": "enforce"}, "commands": {"test": "exit 1"}}))
    repo.write("app.py", "x\n")
    proc = run_hook(script, commit_event(repo), repo.path, {"GITHUB_ACTIONS": "true"})
    assert (proc.returncode, proc.stdout) == (0, "")
