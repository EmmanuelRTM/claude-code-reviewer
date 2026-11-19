"""The command line: arguments, `--set` / `--config`, output and exit codes."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from installer_kit import snapshot

from installer import cli, fsops
from installer.cli import main
from installer.kitlib import REPO_ROOT

pytest_plugins = ("installer_fixtures",)

CONFIG = ".claude/reviewkit.json"
GATE = ".claude/hooks/gate.py"


def run(kit, *arguments: str) -> int:
    """Run the command line against the fixture kit instead of the real payload."""
    return main(list(arguments), payload_root=kit.payload, blocks_root=kit.blocks)


def config_of(repo) -> dict:
    return json.loads((repo.path / CONFIG).read_text(encoding="utf-8"))


# --- success --------------------------------------------------------------------------


def test_install_returns_zero_and_prints_the_report_and_next_steps(kit, repo, capsys: pytest.CaptureFixture) -> None:
    assert run(kit, "--target", str(repo.path)) == 0
    output = capsys.readouterr().out
    assert (repo.path / GATE).exists()
    lines = output.splitlines()
    assert any(line.split() == ["created", GATE] for line in lines)
    assert any(line.split() == ["created", "REVIEW.md"] for line in lines)
    assert any(line.split() == ["merged", ".gitignore"] for line in lines)
    assert "Next steps:" in output
    for fact in ("CLAUDE_CODE_OAUTH_TOKEN", "claude setup-token", "https://github.com/apps/claude", "rk.py doctor"):
        assert fact in output


def test_a_second_run_reports_unchanged_and_kept(kit, repo, capsys: pytest.CaptureFixture) -> None:
    run(kit, "--target", str(repo.path))
    capsys.readouterr()
    assert run(kit, "--target", str(repo.path)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.split() == ["unchanged", GATE] for line in lines)
    assert any(line.split() == ["kept", "(user-owned)", "REVIEW.md"] for line in lines)
    assert not any(line.strip().startswith(("created", "updated", "merged", "removed")) for line in lines)


def test_dry_run_prints_the_plan_and_writes_nothing(kit, repo, capsys: pytest.CaptureFixture) -> None:
    before = snapshot(repo.path)
    assert run(kit, "--target", str(repo.path), "--dry-run") == 0
    output = capsys.readouterr().out
    assert snapshot(repo.path) == before
    assert "dry run" in output.lower()
    assert any(line.split() == ["created", GATE] for line in output.splitlines())
    assert "Next steps" not in output


def test_set_values_are_coerced_and_written(kit, repo) -> None:
    code = run(
        kit,
        "--target",
        str(repo.path),
        "--set",
        "commands.test=npm test",
        "--set",
        "gate.mode=enforce",
        "--set",
        "owners=@org/leads,@alice",
        "--set",
        "checks.on_stop=lint,test",
        "--set",
        "checks.timeout_seconds=900",
        "--set",
        "gate.require_trailer=false",
    )
    assert code == 0
    config = config_of(repo)
    assert config["commands"]["test"] == "npm test"
    assert config["gate"]["mode"] == "enforce"
    assert config["owners"] == ["@org/leads", "@alice"]
    assert config["checks"]["on_stop"] == ["lint", "test"]
    assert config["checks"]["timeout_seconds"] == 900
    assert config["gate"]["require_trailer"] is False
    assert "- Test: `npm test`" in (repo.path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "/REVIEW.md                      @org/leads @alice" in (repo.path / ".github/CODEOWNERS").read_text()


def test_config_file_is_the_base_and_set_goes_on_top(kit, repo, tmp_path: Path) -> None:
    repo.write("go.mod", "module demo\n")
    source = tmp_path / "base.json"
    source.write_text(json.dumps({"commands": {"test": "from file", "lint": "from file"}, "gate": {"mode": "off"}}))
    assert run(kit, "--target", str(repo.path), "--config", str(source), "--set", "commands.test=from set") == 0
    config = config_of(repo)
    assert config["commands"]["test"] == "from set"  # --set beats --config
    assert config["commands"]["lint"] == "from file"  # --config beats detection
    assert config["commands"]["build"] == "go build ./..."  # detection beats the default
    assert config["gate"]["mode"] == "off"
    assert config["review"]["level"] == "quick"  # default


def test_set_updates_an_existing_configuration(kit, repo) -> None:
    run(kit, "--target", str(repo.path), "--set", "commands.test=first")
    assert run(kit, "--target", str(repo.path), "--set", "commands.lint=second") == 0
    assert config_of(repo)["commands"]["test"] == "first"
    assert config_of(repo)["commands"]["lint"] == "second"


def test_no_detect(kit, repo) -> None:
    repo.write("go.mod", "module demo\n")
    assert run(kit, "--target", str(repo.path), "--no-detect") == 0
    assert config_of(repo)["commands"]["test"] == ""


def test_force_overwrites_local_modifications(kit, repo, capsys: pytest.CaptureFixture) -> None:
    run(kit, "--target", str(repo.path))
    (repo.path / GATE).write_text("print('my edit')\n", encoding="utf-8")
    capsys.readouterr()
    assert run(kit, "--target", str(repo.path)) == 0
    output = capsys.readouterr().out
    assert any(line.split() == ["skipped", "(locally", "modified)", GATE] for line in output.splitlines())
    assert "--force" in output
    assert run(kit, "--target", str(repo.path), "--force") == 0
    assert (repo.path / GATE).read_text(encoding="utf-8") == "print('gate')\n"


def test_uninstall(kit, repo, capsys: pytest.CaptureFixture) -> None:
    run(kit, "--target", str(repo.path))
    capsys.readouterr()
    assert run(kit, "--target", str(repo.path), "--uninstall") == 0
    output = capsys.readouterr().out
    assert not (repo.path / GATE).exists()
    assert (repo.path / "REVIEW.md").exists()
    assert any(line.split() == ["removed", GATE] for line in output.splitlines())
    assert any(line.split() == ["kept", "(user-owned)", "REVIEW.md"] for line in output.splitlines())
    assert "left in place" in output
    assert "Next steps" not in output


# --- exit code 2: usage and validation -------------------------------------------------------


def test_a_target_that_is_not_a_repository_is_refused(kit, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert run(kit, "--target", str(plain)) == 2
    captured = capsys.readouterr()
    assert "not inside a git work tree" in captured.err
    assert captured.err.startswith("error:")
    assert list(plain.iterdir()) == []


def test_a_subdirectory_or_missing_target_is_refused(kit, repo, capsys: pytest.CaptureFixture) -> None:
    (repo.path / "sub").mkdir()
    assert run(kit, "--target", str(repo.path / "sub")) == 2
    assert "top level" in capsys.readouterr().err
    assert run(kit, "--target", str(repo.path / "missing")) == 2
    assert "not an existing directory" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [[], ["--bogus"], ["--target"], ["--target", "x", "--set"], ["--target", "x", "extra"]],
)
def test_usage_errors(kit, capsys: pytest.CaptureFixture, arguments: list) -> None:
    assert run(kit, *arguments) == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("assignment", "named"),
    [
        ("commands.tset=x", "commands.tset"),  # unknown key
        ("checks.timeout_seconds=soon", "checks.timeout_seconds"),  # not an integer
        ("gate.require_trailer=maybe", "gate.require_trailer"),  # not a boolean
        ("gate.mode=bogus", "gate.mode"),  # well typed, rejected by the library's validation
        ("checks.on_stop=deploy", "checks.on_stop"),
        ("owners=alice", "owners"),  # not something CODEOWNERS accepts
        ("novalue", "KEY=VALUE"),
    ],
)
def test_bad_settings_are_rejected_by_name_before_anything_is_written(
    kit, repo, capsys: pytest.CaptureFixture, assignment: str, named: str
) -> None:
    before = snapshot(repo.path)
    assert run(kit, "--target", str(repo.path), "--set", assignment) == 2
    assert named in capsys.readouterr().err
    assert snapshot(repo.path) == before


@pytest.mark.parametrize("content", [None, "{broken", "[1, 2]"])
def test_an_unusable_config_file_is_refused(kit, repo, tmp_path: Path, capsys: pytest.CaptureFixture, content) -> None:
    source = tmp_path / "base.json"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    before = snapshot(repo.path)
    assert run(kit, "--target", str(repo.path), "--config", str(source)) == 2
    assert "--config" in capsys.readouterr().err
    assert snapshot(repo.path) == before


def test_a_corrupt_existing_configuration_is_refused(kit, repo, capsys: pytest.CaptureFixture) -> None:
    repo.write(CONFIG, "{broken")
    assert run(kit, "--target", str(repo.path)) == 2
    assert CONFIG in capsys.readouterr().err
    assert (repo.path / CONFIG).read_text(encoding="utf-8") == "{broken"
    assert not (repo.path / GATE).exists()


def test_uninstall_does_not_take_settings(kit, repo, capsys: pytest.CaptureFixture) -> None:
    run(kit, "--target", str(repo.path))
    capsys.readouterr()
    assert run(kit, "--target", str(repo.path), "--uninstall", "--set", "gate.mode=off") == 2
    assert "--uninstall" in capsys.readouterr().err
    assert (repo.path / GATE).exists()


def test_a_path_that_could_not_be_handled_gives_exit_code_two(kit, repo, capsys: pytest.CaptureFixture) -> None:
    repo.write(".claude/settings.json", "{broken")
    assert run(kit, "--target", str(repo.path)) == 2
    output = capsys.readouterr().out
    assert any(line.strip().startswith("error") and ".claude/settings.json:" in line for line in output.splitlines())
    assert "Next steps" not in output
    assert (repo.path / GATE).exists()  # the rest of the install went through


# --- exit code 1: unexpected failure ------------------------------------------------------------


def test_an_unexpected_failure_gives_exit_code_one(kit, repo, monkeypatch, capsys: pytest.CaptureFixture) -> None:
    def explode(*arguments, **options):
        raise RuntimeError("the unexpected happened")

    monkeypatch.setattr(cli, "install", explode)
    assert run(kit, "--target", str(repo.path)) == 1
    assert "the unexpected happened" in capsys.readouterr().err


def test_a_failed_write_gives_exit_code_one_and_says_it_is_safe_to_retry(
    kit, repo, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    def full_disk(source, destination):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(fsops.os, "replace", full_disk)
        assert run(kit, "--target", str(repo.path)) == 1
    error = capsys.readouterr().err
    assert "No space left on device" in error
    assert "again" in error
    assert run(kit, "--target", str(repo.path)) == 0  # and a retry completes the install
    assert (repo.path / GATE).exists()


# --- output that cannot be delivered ----------------------------------------------------------


def test_a_reader_that_went_away_does_not_turn_success_into_failure(kit, repo, monkeypatch) -> None:
    class ClosedPipe(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(sys, "stdout", ClosedPipe())  # as in `install.py ... | grep -q created`
    assert run(kit, "--target", str(repo.path)) == 0
    assert (repo.path / GATE).exists()


def test_a_console_that_cannot_show_a_character_still_gets_the_report(kit, make_repo, monkeypatch) -> None:
    repo = make_repo("caf\u00e9-\u4e2d")
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    assert run(kit, "--target", str(repo.path)) == 0
    stream.flush()
    output = stream.buffer.getvalue().decode("ascii")
    assert "caf\\xe9-\\u4e2d" in output
    assert "Next steps:" in output


# --- the script itself ------------------------------------------------------------------------


def test_help(capsys: pytest.CaptureFixture) -> None:
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    for option in ("--target", "--set", "--config", "--dry-run", "--force", "--no-detect", "--uninstall"):
        assert option in output


def test_the_entry_point_script_runs_in_isolated_mode(tmp_path: Path) -> None:
    script = REPO_ROOT / "install.py"
    helped = subprocess.run(
        [sys.executable, "-I", str(script), "--help"], capture_output=True, text=True, cwd=tmp_path, timeout=60
    )
    assert helped.returncode == 0
    assert "--target" in helped.stdout
    refused = subprocess.run(
        [sys.executable, "-I", str(script), "--target", str(tmp_path / "nowhere")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )
    assert refused.returncode == 2
    assert "error:" in refused.stderr
