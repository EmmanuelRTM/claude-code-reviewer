"""`rk.py doctor`: output, exit codes, and the optional GitHub half."""

from __future__ import annotations

import json

import pytest

from doctor_kit import install_payload
from reviewkit import cli, cmd_doctor
from reviewkit.doctor.model import FAIL, OK, SKIP, WARN, Check, exit_code, render


@pytest.fixture
def installed(repo, monkeypatch):
    table = "| ID | Trigger | Check | Origin |\n|--|--|--|--|\n| RC-01 | A log line changes | No tokens are logged | PR #12 |\n"
    install_payload(repo, commit=False)
    repo.write("docs/reliability-checklist.md", table)
    repo.commit("install review kit")
    monkeypatch.chdir(repo.path)
    return repo


def test_exit_code_reflects_failures_and_strict_also_warnings():
    ok, warn, fail, skip = (Check("a", s, "x") for s in (OK, WARN, FAIL, SKIP))
    assert exit_code([ok, skip]) == 0
    assert exit_code([ok, warn]) == 0
    assert exit_code([ok, warn], strict=True) == 1
    assert exit_code([ok, fail]) == 1


def test_render_lists_each_check_with_its_fix_and_a_tally():
    text = render([Check("config", OK, "valid"), Check("hooks", FAIL, "commit gate not wired", "Run the installer")])
    lines = text.splitlines()
    assert lines[0].startswith("ok") and "config" in lines[0]
    assert lines[1].startswith("FAIL") and "commit gate not wired" in lines[1]
    assert "Run the installer" in lines[2]
    assert "1 ok" in lines[-1] and "1 failed" in lines[-1]


def test_a_healthy_repository_exits_zero(installed, capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "0 failed" in out and "0 warnings" in out


def test_a_failure_exits_one(installed, capsys):
    (installed.path / ".claude" / "hooks" / "verify_commit_gate.py").unlink()
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_strict_turns_warnings_into_a_failing_exit(installed, capsys):
    (installed.path / ".github" / "CODEOWNERS").unlink()
    assert cli.main(["doctor"]) == 0
    assert cli.main(["doctor", "--strict"]) == 1


def test_json_output_is_machine_readable(installed, capsys):
    assert cli.main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert {"id", "status", "summary", "fix"} <= set(data["checks"][0])


def test_project_option_checks_another_directory(installed, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor", "--project", str(installed.path)]) == 0


def test_github_checks_run_only_when_asked(installed, github, monkeypatch, capsys):
    monkeypatch.setattr(cmd_doctor, "client_from_env", lambda env=None: pytest.fail("no network without --github"))
    assert cli.main(["doctor"]) == 0


def test_github_checks_use_the_named_repository(installed, github, monkeypatch, capsys):
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(cmd_doctor, "client_from_env", lambda env=None: github)
    github.on("GET", r"/repos/octo/app", {"has_issues": False, "default_branch": "main"})
    assert cli.main(["doctor", "--github", "--repo", "octo/app"]) == 1
    out = capsys.readouterr().out
    assert "gh-repo" in out and "Issues" in out


def test_github_checks_without_a_token_are_skipped_not_failed(installed, monkeypatch, capsys):
    monkeypatch.setattr(cmd_doctor, "discover_token", lambda env=None: "")
    assert cli.main(["doctor", "--github", "--repo", "octo/app"]) == 0
    assert "token" in capsys.readouterr().out


def test_the_repository_is_read_from_the_origin_remote(installed, github, monkeypatch, capsys):
    installed.git("remote", "add", "origin", "https://github.com/octo/app.git")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(cmd_doctor, "client_from_env", lambda env=None: github)
    github.on("GET", r"/repos/octo/app", {"has_issues": True, "default_branch": "main"})
    cli.main(["doctor", "--github"])
    assert github.calls_to("GET", r"/repos/octo/app")


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/octo/app.git", "octo/app"),
        ("https://github.com/octo/app", "octo/app"),
        ("git@github.com:octo/app.git", "octo/app"),
        ("ssh://git@github.com/octo/app.git", "octo/app"),
        ("http://local_proxy@127.0.0.1:1234/git/octo/app", "octo/app"),
        ("/srv/git/app.git", ""),
        ("", ""),
    ],
)
def test_repository_name_from_remote_url(url, expected):
    assert cmd_doctor.repo_from_remote(url) == expected
