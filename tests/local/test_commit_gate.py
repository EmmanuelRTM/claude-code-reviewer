"""PreToolUse commit gate: off / observe / enforce, opt-out, exemptions, escape hatch."""

from __future__ import annotations

import json

import pytest

from reviewkit.fingerprint import tree_fingerprint
from reviewkit.hooks import commit_gate
from reviewkit.localstate import LocalState


def event(command, session="s1", cwd=None, tool="Bash"):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
        "session_id": session,
        "cwd": str(cwd) if cwd else None,
    }


def configure(repo, **gate):
    repo.write(".claude/reviewkit.json", json.dumps({"gate": gate}))
    repo.commit("configure gate")


def mark_verified(repo):
    LocalState(repo.path).write_json("verified.json", {"fingerprint": tree_fingerprint(repo.path)})


def run(repo, command, env=None, session="s1"):
    return commit_gate.evaluate(event(command, session=session, cwd=repo.path), env or {})


def log_events(repo):
    return [entry["event"] for entry in LocalState(repo.path).read_lines("gate.log")]


COMMIT = 'git commit -m "feat: x" -m "Verified-By: verify, code-review"'


def test_other_tools_and_other_commands_are_ignored(repo):
    assert commit_gate.evaluate(event("git commit -m x", cwd=repo.path, tool="Read"), {}).exit_code == 0
    result = run(repo, "git status")
    assert result.exit_code == 0 and result.stdout is None
    assert log_events(repo) == []


def test_off_mode_does_nothing(repo):
    configure(repo, mode="off")
    repo.write("app.py", "x\n")
    assert run(repo, COMMIT).exit_code == 0
    assert log_events(repo) == []


def test_observe_mode_allows_but_records_and_tells_claude(repo):
    repo.write("app.py", "x\n")
    result = run(repo, COMMIT)
    assert result.exit_code == 0
    context = result.stdout["hookSpecificOutput"]
    assert context["hookEventName"] == "PreToolUse"
    assert "verify" in context["additionalContext"]
    assert "permissionDecision" not in context
    assert log_events(repo) == ["would_block:unverified"]


def test_verified_changes_pass_silently(repo):
    repo.write("app.py", "x\n")
    mark_verified(repo)
    result = run(repo, COMMIT)
    assert result.exit_code == 0 and result.stdout is None
    assert log_events(repo) == ["verified"]


def test_marker_goes_stale_when_the_tree_changes(repo):
    repo.write("app.py", "x\n")
    mark_verified(repo)
    repo.write("app.py", "y\n")
    run(repo, COMMIT)
    assert log_events(repo) == ["would_block:unverified"]


def test_marker_survives_the_commit_itself(repo):
    repo.write("app.py", "x\n")
    mark_verified(repo)
    repo.commit("the verified change")
    run(repo, "git commit --amend -m 'reword' -m 'Verified-By: verify'")
    assert log_events(repo) == ["verified"]


def test_enforce_blocks_with_exit_2_and_a_reason_for_claude(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    result = run(repo, COMMIT)
    assert result.exit_code == 2
    assert "verify" in result.stderr and "Verified-By: none" in result.stderr
    assert log_events(repo) == ["blocked:unverified"]


def test_enforce_lets_the_third_attempt_through_so_a_session_cannot_get_stuck(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    codes = [run(repo, COMMIT).exit_code for _ in range(4)]
    assert codes == [2, 2, 0, 0]
    assert log_events(repo) == [
        "blocked:unverified",
        "blocked:unverified",
        "allowed_after_max_blocks",
        "allowed_after_max_blocks",
    ]


def test_block_counter_is_per_session(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    assert [run(repo, COMMIT, session="a").exit_code for _ in range(3)] == [2, 2, 0]
    assert run(repo, COMMIT, session="b").exit_code == 2


def test_opt_out_with_a_reason_is_always_allowed_and_logged(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    result = run(repo, "git commit -m 'chore' -m 'Verified-By: none (generated file, nothing to run)'")
    assert result.exit_code == 0
    assert log_events(repo) == ["opt_out"]


def test_opt_out_without_a_reason_is_not_an_opt_out(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    result = run(repo, "git commit -m 'chore' -m 'Verified-By: none'")
    assert result.exit_code == 2
    assert "reason" in result.stderr


def test_documentation_only_changes_are_exempt(repo):
    configure(repo, mode="enforce")
    repo.write("docs/guide.md", "# guide\n")
    repo.write("NOTES.md", "notes\n")
    assert run(repo, "git commit -m 'docs'").exit_code == 0
    assert log_events(repo) == ["exempt"]
    repo.write("app.py", "x\n")
    assert run(repo, "git commit -m 'docs and code'").exit_code == 2


def test_missing_trailer_is_flagged_even_when_verified(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    mark_verified(repo)
    result = run(repo, "git commit -m 'feat: x'")
    assert result.exit_code == 2 and "Verified-By" in result.stderr
    assert log_events(repo) == ["blocked:missing_trailer"]


def test_trailer_not_required_when_the_message_is_not_visible_or_disabled(repo):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    mark_verified(repo)
    assert run(repo, "git commit").exit_code == 0
    configure(repo, mode="enforce", require_trailer=False)
    mark_verified(repo)
    assert run(repo, "git commit -m 'feat: x'").exit_code == 0


def test_environment_can_override_the_committed_mode(repo):
    repo.write("app.py", "x\n")
    assert run(repo, COMMIT, env={"VERIFY_GATE_MODE": "enforce"}).exit_code == 2
    assert run(repo, COMMIT, env={"VERIFY_GATE_MODE": "off"}).exit_code == 0


@pytest.mark.parametrize("env", [{"GITHUB_ACTIONS": "true"}, {"REVIEWKIT_DISABLE": "1"}])
def test_never_runs_in_ci_or_when_disabled(repo, env):
    configure(repo, mode="enforce")
    repo.write("app.py", "x\n")
    assert run(repo, COMMIT, env=env).exit_code == 0
    assert log_events(repo) == []


def test_fails_open_outside_a_repository_and_on_a_broken_config(tmp_path, repo):
    assert commit_gate.evaluate(event(COMMIT, cwd=tmp_path), {}).exit_code == 0
    repo.write(".claude/reviewkit.json", "{broken")
    result = run(repo, COMMIT)
    assert result.exit_code == 0 and "reviewkit" in result.stderr


def test_uses_the_work_tree_claude_is_in_not_the_session_root(repo, tmp_path):
    linked = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "--detach", str(linked))
    (linked / "app.py").write_text("x\n")
    result = commit_gate.evaluate(event(COMMIT, cwd=linked), {"CLAUDE_PROJECT_DIR": str(repo.path)})
    assert result.exit_code == 0
    assert [e["event"] for e in LocalState(linked).read_lines("gate.log")] == ["would_block:unverified"]


def test_main_reads_stdin_and_never_crashes(repo, monkeypatch, capsys):
    import io

    repo.write("app.py", "x\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event(COMMIT, cwd=repo.path))))
    assert commit_gate.main() == 0
    assert "additionalContext" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert commit_gate.main() == 0


def test_a_directory_outside_any_repository_never_falls_back_to_another_one(tmp_path, repo, monkeypatch):
    # Regression: with the event's cwd outside a repository, the hook used the process's own
    # working directory instead and judged (and logged into) an unrelated repository.
    monkeypatch.chdir(repo.path)
    repo.write("app.py", "x\n")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = commit_gate.evaluate(event(COMMIT, cwd=outside), {"VERIFY_GATE_MODE": "enforce"})
    assert result.exit_code == 0
    assert log_events(repo) == []
