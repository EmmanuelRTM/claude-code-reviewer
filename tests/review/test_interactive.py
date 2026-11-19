"""Arguments for the `@claude` workflow: the project's checks become runnable, the contract is restated."""

from __future__ import annotations

import shlex

from reviewkit import cli
from reviewkit.config import parse_config
from reviewkit.interactive import interactive_args


def args_for(**config):
    return shlex.split(interactive_args(parse_config(config)[0]))


def pairs(tokens):
    return dict(zip(tokens[::2], tokens[1::2]))


def test_configured_checks_are_allowed_exactly():
    found = pairs(args_for(commands={"test": "npm run test", "lint": "ruff check .", "build": ""}))
    assert found["--allowedTools"] == "Bash(npm run test),Bash(ruff check .)"
    assert found["--max-turns"] == "60"
    assert "--model" not in found


def test_commands_that_need_a_shell_to_parse_are_not_pre_approved():
    risky = {"test": "pytest -q && echo 'done'", "lint": "make lint; rm -rf /", "build": "a | b"}
    tokens = args_for(commands=risky)
    assert "--allowedTools" not in tokens


def test_model_and_turns_come_from_config():
    found = pairs(args_for(interactive={"max_turns": 25, "model": "sonnet"}))
    assert found["--max-turns"] == "25" and found["--model"] == "sonnet"


def test_the_contract_is_restated_without_quotes_that_would_break_the_arguments():
    raw = interactive_args(parse_config({"commands": {"test": "pytest -q"}})[0])
    found = pairs(shlex.split(raw))
    prompt = found["--append-system-prompt"]
    assert "pytest -q" in prompt and "Verified-By" in prompt and "three" in prompt
    assert "'" not in prompt and '"' not in prompt and "$" not in prompt


def test_cli_writes_the_step_output(tmp_path, monkeypatch, repo):
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.chdir(repo.path)
    assert cli.main(["interactive", "args"]) == 0
    assert output.read_text().startswith("claude_args=--max-turns 60")
