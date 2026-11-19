"""Running the project's configured checks (lint, build, test)."""

from __future__ import annotations

import sys

import pytest

from reviewkit.checks import detect_zero_run, format_failure, run_check, run_checks, tail_of
from reviewkit.config import parse_config

PY = sys.executable


def test_passing_command(tmp_path):
    result = run_check("lint", f'{PY} -c "print(123)"', tmp_path, timeout=30)
    assert result.status == "pass" and result.exit_code == 0 and result.ok
    assert "123" in result.tail


def test_failing_command_keeps_the_exit_code_and_output(tmp_path):
    result = run_check("test", f"{PY} -c \"import sys; print('boom'); sys.exit(3)\"", tmp_path, timeout=30)
    assert result.status == "fail" and result.exit_code == 3 and not result.ok
    assert "boom" in result.tail


def test_stderr_is_captured(tmp_path):
    result = run_check("build", f"{PY} -c \"import sys; sys.stderr.write('warn'); sys.exit(1)\"", tmp_path, timeout=30)
    assert "warn" in result.tail


def test_runs_in_the_given_directory(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    result = run_check("lint", f"{PY} -c \"import os; print(sorted(os.listdir('.')))\"", tmp_path, timeout=30)
    assert "marker.txt" in result.tail


def test_timeout_kills_the_whole_process_tree(tmp_path):
    result = run_check("test", f'{PY} -c "import time; time.sleep(30)"', tmp_path, timeout=0.5)
    assert result.status == "timeout" and not result.ok
    assert result.duration < 10


def test_missing_program_is_a_failure_not_a_crash(tmp_path):
    result = run_check("test", "definitely-not-a-real-program-xyz --flag", tmp_path, timeout=30)
    assert not result.ok


@pytest.mark.parametrize(
    "output",
    [
        "============ no tests ran in 0.01s ============",
        "collected 0 items\n",
        "No tests found, exiting with code 0",
        "No test files found, exiting with code 0",
        "Ran 0 tests in 0.000s\n\nOK",
        "running 0 tests\n\ntest result: ok. 0 passed; 0 failed",
        "?   \texample.com/pkg\t[no test files]\n",
        "0 examples, 0 failures",
        "  0 passing (1ms)",
    ],
)
def test_zero_run_is_detected(output):
    assert detect_zero_run(output)


@pytest.mark.parametrize(
    "output",
    [
        "collected 12 items\n12 passed in 0.3s",
        "Tests: 4 passed, 4 total",
        "Ran 3 tests in 0.010s\n\nOK",
        "running 0 tests\n\nrunning 7 tests\ntest result: ok. 7 passed",
        "ok  \texample.com/pkg\t0.01s\n?   \texample.com/other\t[no test files]",
        "10 passing (20ms)",
        "",
    ],
)
def test_real_runs_are_not_zero_runs(output):
    assert not detect_zero_run(output)


def test_exit_zero_with_no_tests_is_not_green(tmp_path):
    result = run_check("test", f"{PY} -c \"print('Ran 0 tests in 0.000s')\"", tmp_path, timeout=30)
    assert result.status == "zero_run" and not result.ok


def test_zero_run_only_applies_to_the_test_check_and_can_be_turned_off(tmp_path):
    command = f"{PY} -c \"print('Ran 0 tests in 0.000s')\""
    assert run_check("lint", command, tmp_path, timeout=30).ok
    assert run_check("test", command, tmp_path, timeout=30, zero_run_is_failure=False).ok


def test_run_checks_follows_configured_order_and_stops_at_the_first_failure(tmp_path):
    cfg, _ = parse_config(
        {
            "commands": {
                "lint": f'{PY} -c "print(1)"',
                "build": f'{PY} -c "import sys; sys.exit(2)"',
                "test": f'{PY} -c "print(3)"',
            },
            "checks": {"on_stop": ["lint", "build", "test"]},
        }
    )
    results = run_checks(cfg, tmp_path)
    assert [(r.name, r.status) for r in results] == [("lint", "pass"), ("build", "fail")]


def test_run_checks_skips_unconfigured_commands(tmp_path):
    cfg, _ = parse_config({"commands": {"test": f'{PY} -c "print(1)"'}, "checks": {"on_stop": ["lint", "test"]}})
    assert [r.name for r in run_checks(cfg, tmp_path)] == ["test"]
    assert run_checks(parse_config({})[0], tmp_path) == []


def test_run_checks_shares_one_time_budget(tmp_path):
    cfg, _ = parse_config(
        {
            "commands": {"lint": f'{PY} -c "import time; time.sleep(30)"', "test": f'{PY} -c "print(1)"'},
            "checks": {"on_stop": ["lint", "test"], "timeout_seconds": 1},
        }
    )
    results = run_checks(cfg, tmp_path)
    assert [(r.name, r.status) for r in results] == [("lint", "timeout")]


def test_tail_strips_ansi_and_bounds_size():
    text = "\n".join(f"\x1b[31mline {i}\x1b[0m" for i in range(500))
    tail = tail_of(text, max_lines=5, max_chars=1000)
    assert tail.splitlines() == [f"line {i}" for i in range(495, 500)]
    assert len(tail_of("x" * 10_000, max_lines=5, max_chars=200)) <= 200


def test_format_failure_names_the_command_and_status(tmp_path):
    result = run_check("test", f"{PY} -c \"import sys; print('E assert 1 == 2'); sys.exit(1)\"", tmp_path, timeout=30)
    text = format_failure(result)
    assert "test" in text and "exit code 1" in text and "E assert 1 == 2" in text
