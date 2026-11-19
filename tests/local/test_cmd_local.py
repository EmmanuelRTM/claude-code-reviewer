"""CLI for the local loop: fingerprint, verification marker, checks, gate report."""

from __future__ import annotations

import json
import sys

import pytest

from reviewkit import cli
from reviewkit.fingerprint import tree_fingerprint
from reviewkit.localstate import LocalState

PY = sys.executable


@pytest.fixture
def in_repo(repo, monkeypatch):
    monkeypatch.chdir(repo.path)
    return repo


def test_fingerprint_prints_the_tree_id(in_repo, capsys):
    assert cli.main(["fingerprint"]) == 0
    assert capsys.readouterr().out.strip() == tree_fingerprint(in_repo.path)


def test_fingerprint_outside_a_repository_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["fingerprint"]) == 1
    assert "git" in capsys.readouterr().err


def test_verify_mark_then_status(in_repo, capsys):
    in_repo.write("app.py", "x\n")
    assert cli.main(["verify", "status"]) == 1
    assert cli.main(["verify", "mark", "--evidence", "drove the signup flow; 3 of 3 passed"]) == 0
    marker = LocalState(in_repo.path).read_json("verified.json", {})
    assert marker["fingerprint"] == tree_fingerprint(in_repo.path)
    assert marker["evidence"].startswith("drove the signup flow")
    assert cli.main(["verify", "status"]) == 0
    in_repo.write("app.py", "y\n")
    assert cli.main(["verify", "status"]) == 1
    assert "not verified" in capsys.readouterr().out


def test_verify_mark_works_from_a_subdirectory(in_repo, monkeypatch):
    sub = in_repo.path / "pkg"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert cli.main(["verify", "mark"]) == 0
    assert LocalState(in_repo.path).read_json("verified.json", {})["fingerprint"] == tree_fingerprint(in_repo.path)


def test_checks_run_reports_each_check_and_fails_on_the_first_failure(in_repo, capsys):
    in_repo.write(
        ".claude/reviewkit.json",
        json.dumps(
            {
                "commands": {
                    "lint": f'{PY} -c "print(1)"',
                    "test": f"{PY} -c \"import sys; print('boom'); sys.exit(4)\"",
                },
                "checks": {"on_stop": ["lint", "test"]},
            }
        ),
    )
    assert cli.main(["checks", "run"]) == 1
    out = capsys.readouterr().out
    assert "lint: pass" in out and "test: fail" in out and "boom" in out
    assert cli.main(["checks", "run", "lint"]) == 0


def test_checks_run_with_nothing_configured_says_so(in_repo, capsys):
    assert cli.main(["checks", "run"]) == 0
    assert "no checks are configured" in capsys.readouterr().out.lower()


def test_gate_report_counts_events(in_repo, capsys):
    state = LocalState(in_repo.path)
    for name in ("verified", "would_block:unverified", "would_block:unverified", "opt_out"):
        state.append_line("gate.log", {"ts": "2026-11-19T10:00:00Z", "event": name, "mode": "observe"})
    assert cli.main(["gate", "report"]) == 0
    out = capsys.readouterr().out
    assert "would_block:unverified" in out and "2" in out and "4 commits" in out


def test_gate_report_with_no_log(in_repo, capsys):
    assert cli.main(["gate", "report"]) == 0
    assert "no commits" in capsys.readouterr().out.lower()


def test_hook_subcommands_delegate_to_the_hooks(in_repo, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})))
    assert cli.main(["hook", "commit-gate"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "cwd": str(in_repo.path)})))
    assert cli.main(["hook", "stop-checks"]) == 0
