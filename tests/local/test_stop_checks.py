"""Stop hook: the project's checks must pass before Claude may finish a turn."""

from __future__ import annotations

import json
import sys

import pytest

from reviewkit.hooks import stop_checks

PY = sys.executable
PASS = f"{PY} -c \"print('ok 1 test')\""
FAIL = f"{PY} -c \"import sys; print('E   assert 1 == 2'); sys.exit(1)\""


def event(repo, session="s1", **extra):
    data = {"hook_event_name": "Stop", "session_id": session, "cwd": str(repo.path), "permission_mode": "default"}
    data.update(extra)
    return data


def configure(repo, test=PASS, **checks):
    repo.write(".claude/reviewkit.json", json.dumps({"commands": {"test": test}, "checks": checks}))
    repo.commit("configure checks")


class CountingRunner:
    """Wraps the real runner so tests can see whether checks actually ran."""

    def __init__(self):
        self.calls = 0

    def __call__(self, cfg, cwd, names=None):
        from reviewkit.checks import run_checks

        self.calls += 1
        return run_checks(cfg, cwd, names)


@pytest.fixture
def runner():
    return CountingRunner()


def stop(repo, runner, env=None, **kw):
    return stop_checks.evaluate(event(repo, **kw), env or {}, runner=runner)


def test_nothing_configured_means_nothing_runs(repo, runner):
    repo.write("app.py", "x\n")
    assert stop(repo, runner).exit_code == 0
    assert runner.calls == 0


def test_a_session_that_changed_nothing_never_runs_checks(repo, runner):
    configure(repo)
    assert stop(repo, runner).exit_code == 0
    assert stop(repo, runner).exit_code == 0
    assert runner.calls == 0


def test_uncommitted_changes_run_the_checks_once_per_tree(repo, runner):
    configure(repo)
    repo.write("app.py", "x\n")
    assert stop(repo, runner).exit_code == 0
    assert stop(repo, runner).exit_code == 0
    assert runner.calls == 1
    repo.write("app.py", "y\n")
    assert stop(repo, runner).exit_code == 0
    assert runner.calls == 2


def test_committing_verified_content_does_not_rerun(repo, runner):
    configure(repo)
    repo.write("app.py", "x\n")
    stop(repo, runner)
    repo.commit("add app")
    stop(repo, runner)
    assert runner.calls == 1


def test_edit_and_commit_inside_one_turn_is_still_checked(repo, runner):
    configure(repo)
    stop(repo, runner)  # clean tree at first sight: baseline only
    repo.write("app.py", "x\n")
    repo.commit("sneaky commit")
    stop(repo, runner)
    assert runner.calls == 1


def test_failure_blocks_with_exit_2_and_the_output_tail(repo, runner):
    configure(repo, test=FAIL)
    repo.write("app.py", "x\n")
    result = stop(repo, runner)
    assert result.exit_code == 2
    assert "E   assert 1 == 2" in result.stderr
    assert "attempt 1 of 3" in result.stderr
    assert "skip" in result.stderr  # reminds Claude not to weaken tests


def test_three_strikes_then_a_human(repo, runner):
    configure(repo, test=FAIL)
    repo.write("app.py", "x\n")
    results = [stop(repo, runner, stop_hook_active=True) for _ in range(4)]
    assert [r.exit_code for r in results] == [2, 2, 2, 0]
    assert "human" in results[3].stdout["systemMessage"]
    # The counter starts over, so the hook enforces again after the human steps in.
    assert stop(repo, runner).exit_code == 2


def test_a_pass_resets_the_strike_counter(repo, runner):
    configure(repo, test=FAIL)
    repo.write("app.py", "x\n")
    assert stop(repo, runner).exit_code == 2
    configure(repo, test=PASS)
    assert stop(repo, runner).exit_code == 0
    configure(repo, test=FAIL)
    repo.write("app.py", "z\n")
    assert "attempt 1 of 3" in stop(repo, runner).stderr


def test_strikes_are_counted_per_session(repo, runner):
    configure(repo, test=FAIL, max_blocks=1)
    repo.write("app.py", "x\n")
    assert [stop(repo, runner, session="a").exit_code for _ in range(2)] == [2, 0]
    assert stop(repo, runner, session="b").exit_code == 2


def test_max_blocks_zero_only_reports(repo, runner):
    configure(repo, test=FAIL, max_blocks=0)
    repo.write("app.py", "x\n")
    result = stop(repo, runner)
    assert result.exit_code == 0 and "failing" in result.stdout["systemMessage"]


def test_files_written_by_the_checks_do_not_cause_a_rerun(repo, runner):
    configure(repo, test=f"{PY} -c \"open('coverage.out', 'w').write('1'); print('ok')\"")
    repo.write("app.py", "x\n")
    stop(repo, runner)
    stop(repo, runner)
    assert runner.calls == 1


def test_zero_tests_is_not_a_pass(repo, runner):
    configure(repo, test=f"{PY} -c \"print('Ran 0 tests in 0.000s')\"")
    repo.write("app.py", "x\n")
    result = stop(repo, runner)
    assert result.exit_code == 2 and "zero tests" in result.stderr


@pytest.mark.parametrize(
    ("env", "extra"),
    [({"GITHUB_ACTIONS": "true"}, {}), ({"REVIEWKIT_DISABLE": "1"}, {}), ({}, {"permission_mode": "plan"})],
)
def test_never_runs_in_ci_when_disabled_or_in_plan_mode(repo, runner, env, extra):
    configure(repo, test=FAIL)
    repo.write("app.py", "x\n")
    assert stop(repo, runner, env=env, **extra).exit_code == 0
    assert runner.calls == 0


def test_fails_open_outside_a_repository_and_on_a_broken_config(tmp_path, repo, runner):
    outside = stop_checks.evaluate({"hook_event_name": "Stop", "cwd": str(tmp_path)}, {}, runner=runner)
    assert outside.exit_code == 0
    repo.write(".claude/reviewkit.json", "{broken")
    result = stop(repo, runner)
    assert result.exit_code == 0 and "reviewkit" in result.stderr
    assert runner.calls == 0
