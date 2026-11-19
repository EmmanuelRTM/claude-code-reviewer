"""The kit's workflows: hardening rules, the subscription-only constraint, and CLI wiring."""

from __future__ import annotations

import re

import pytest
from workflow_rules import CLAUDE_ACTION, PINNED, check_workflow, load, steps, triggers

from conftest import KIT_ROOT, REPO_ROOT
from reviewkit import cli

WORKFLOWS = sorted((KIT_ROOT / ".github" / "workflows").glob("*.yml"))

BAD_WORKFLOW = """
name: bad
on:
  pull_request:
  pull_request_target:
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - run: echo "${{ github.event.pull_request.title }}"
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
"""


def test_the_rules_catch_every_planted_violation():
    problems = "\n".join(check_workflow(BAD_WORKFLOW, "bad"))
    for expected in (
        "top-level permissions",
        "must declare its permissions",
        "must set timeout-minutes",
        "not pinned to a commit: actions/checkout@v6",
        "interpolates github.event",
        "must authenticate with the subscription token",
        "must not reference an API key",
        "holds the subscription token but runs on ['pull_request']",
        "must not set ref",
    ):
        assert expected in problems, expected


def test_there_are_workflows_to_check():
    names = {path.name for path in WORKFLOWS}
    assert {"claude-review.yml", "claude.yml"} <= names


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_satisfies_the_hardening_rules(path):
    assert check_workflow(path.read_text(), path.name) == []


def test_actions_are_pinned_and_the_check_is_not_vacuous():
    uses = [step["uses"] for path in WORKFLOWS for _job, step in steps(load(path.read_text())) if "uses" in step]
    assert len(uses) >= 4
    assert all(PINNED.match(value) for value in uses)
    assert any(value.startswith(CLAUDE_ACTION) for value in uses)


def test_every_pin_carries_its_version_as_a_comment():
    for path in WORKFLOWS:
        for line in path.read_text().splitlines():
            if re.search(r"uses: .+@[0-9a-f]{40}", line):
                assert re.search(r"# v\d+", line), f"{path.name}: {line.strip()}"


def test_every_cli_command_a_workflow_runs_exists():
    parser = cli.build_parser()
    commands = set()
    for path in WORKFLOWS:
        commands |= set(re.findall(r"rk\.py ([a-z-]+ [a-z-]+)", path.read_text()))
    assert commands, "no workflow runs the kit CLI"
    for command in sorted(commands):
        args, _unknown = parser.parse_known_args(command.split())
        assert getattr(args, "handler", None) is not None, command


def review_workflow():
    return load((KIT_ROOT / ".github" / "workflows" / "claude-review.yml").read_text())


def test_review_runs_only_from_trusted_refs():
    assert triggers(review_workflow()) == {"pull_request_target", "issue_comment", "workflow_dispatch"}


def test_the_job_that_talks_to_claude_cannot_write_to_github():
    job = review_workflow()["jobs"]["review"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    claude = next(step for step in job["steps"] if step.get("uses", "").startswith(CLAUDE_ACTION))
    assert claude["with"]["github_token"] == "${{ github.token }}"  # the job token, not a broader app token
    assert claude["continue-on-error"] is True  # a failed session is reported by the publish job
    assert claude["with"]["prompt"] == "${{ steps.prepare.outputs.prompt }}"


def test_the_job_that_writes_never_sees_the_claude_token_or_the_model():
    text = (KIT_ROOT / ".github" / "workflows" / "claude-review.yml").read_text()
    publish_section = text[text.index("  publish:") :]
    assert "secrets." not in publish_section and CLAUDE_ACTION not in publish_section
    job = review_workflow()["jobs"]["publish"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write", "issues": "write", "checks": "write"}
    assert job["needs"] == "review"
    env = job["steps"][-1]["env"]
    assert set(env) == {"GH_TOKEN", "PLAN", "REVIEW_RESULT", "REVIEW_OUTCOME"}


def test_ordinary_comments_cannot_cancel_a_running_review():
    workflow = review_workflow()
    group = workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert "github.run_id" in group and "startsWith(github.event.comment.body, '@claude review')" in group
    assert "startsWith(github.event.comment.body, '@claude review')" in workflow["jobs"]["review"]["if"]


def test_interactive_workflow_leaves_review_commands_to_the_reviewer():
    workflow = load((KIT_ROOT / ".github" / "workflows" / "claude.yml").read_text())
    assert triggers(workflow) == {"issue_comment", "issues"}
    condition = workflow["jobs"]["claude"]["if"]
    assert "!startsWith(github.event.comment.body, '@claude review')" in condition
    assert "author_association" in condition


def test_the_kits_own_ci_follows_the_same_hardening_rules():
    path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert check_workflow(path.read_text(encoding="utf-8"), path.name) == []
    assert "secrets." not in path.read_text(encoding="utf-8")  # it needs no secret and must not get one
