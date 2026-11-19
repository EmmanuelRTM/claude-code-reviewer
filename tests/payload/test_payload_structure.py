"""The Claude Code files the kit installs: valid frontmatter, least privilege, no drift."""

from __future__ import annotations

import json
import re

import pytest
import yaml

from conftest import KIT_ROOT
from reviewkit.config import Config
from reviewkit.review.schema import REVIEW_SCHEMA
from reviewkit.sanitize import redact_secrets

CLAUDE = KIT_ROOT / ".claude"
SKILLS = sorted((CLAUDE / "skills").glob("*/SKILL.md"))
AGENTS = sorted((CLAUDE / "agents").glob("*.md"))

# Field names documented for Claude Code skills and subagents. An unknown field is ignored
# silently by Claude Code, so a typo here would quietly change behavior.
SKILL_FIELDS = {
    "name",
    "description",
    "when_to_use",
    "argument-hint",
    "arguments",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "disallowed-tools",
    "model",
    "effort",
    "context",
    "agent",
    "background",
    "hooks",
    "paths",
    "shell",
    "metadata",
    "license",
    "compatibility",
}
AGENT_FIELDS = {
    "name",
    "description",
    "tools",
    "disallowedTools",
    "model",
    "permissionMode",
    "maxTurns",
    "autoCompactWindow",
    "skills",
    "mcpServers",
    "hooks",
    "memory",
    "background",
    "omitClaudeMd",
    "effort",
    "isolation",
    "color",
    "initialPrompt",
    "experimental",
}
MODELS = {"sonnet", "opus", "haiku", "fable", "inherit"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VISIBLE_REASONING = re.compile(
    r"think step by step|chain of thought|show your (reasoning|thinking)|reason out loud", re.I
)


def parse(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: frontmatter must start on the first line"
    _, front, body = text.split("---\n", 2)
    data = yaml.safe_load(front)
    assert isinstance(data, dict), path
    return data, body


def tools_of(data):
    raw = data.get("tools", "")
    return [t.strip() for t in raw.split(",")] if isinstance(raw, str) else list(raw)


def test_the_expected_files_exist():
    assert {p.parent.name for p in SKILLS} == {"bug-finding", "finding-verification", "pr-review", "verify"}
    assert {p.stem for p in AGENTS} == {"bug-finder", "finding-verifier"}


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_frontmatter(path):
    data, body = parse(path)
    assert set(data) <= SKILL_FIELDS, f"unknown fields: {set(data) - SKILL_FIELDS}"
    assert data["name"] == path.parent.name and KEBAB.match(data["name"]) and len(data["name"]) <= 64
    assert 20 <= len(data["description"]) <= 1024
    assert body.strip()
    assert len(path.read_text().splitlines()) <= 500
    assert not VISIBLE_REASONING.search(body)


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agent_frontmatter(path):
    data, body = parse(path)
    assert set(data) <= AGENT_FIELDS, f"unknown fields: {set(data) - AGENT_FIELDS}"
    assert data["name"] == path.stem and KEBAB.match(data["name"])
    assert 20 <= len(data["description"]) <= 1024
    assert data["model"] in MODELS and data["effort"] in EFFORTS
    assert isinstance(data["maxTurns"], int) and data["maxTurns"] > 0
    assert body.strip() and not VISIBLE_REASONING.search(body)
    assert "Done when" in body  # every delegation has a checkable end


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_review_agents_are_read_only_and_cannot_delegate(path):
    data, _ = parse(path)
    assert sorted(tools_of(data)) == ["Glob", "Grep", "Read"]


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agents_preload_only_skills_that_exist_and_can_be_preloaded(path):
    data, _ = parse(path)
    skills = data.get("skills")
    assert isinstance(skills, list) and 1 <= len(skills) <= 4
    for name in skills:
        skill_data, _ = parse(CLAUDE / "skills" / name / "SKILL.md")
        # A skill with disable-model-invocation cannot be preloaded into a subagent.
        assert skill_data.get("disable-model-invocation") is not True, name


def test_the_judge_is_independent_of_the_finders():
    finder, _ = parse(CLAUDE / "agents" / "bug-finder.md")
    verifier, _ = parse(CLAUDE / "agents" / "finding-verifier.md")
    assert verifier["model"] != finder["model"]
    assert not set(verifier["skills"]) & set(finder["skills"])
    defaults = Config().review.models
    assert (defaults.finder, defaults.verifier) == (finder["model"], verifier["model"])
    # Finders are not all on one model.
    assert len({defaults.finder, defaults.finder_light, defaults.finder_deep}) >= 2


def test_the_lead_skill_is_manual_only_and_its_briefs_state_a_done_criterion():
    data, body = parse(CLAUDE / "skills" / "pr-review" / "SKILL.md")
    assert data["disable-model-invocation"] is True
    briefs = re.findall(r"```text\n(.*?)```", body, re.S)
    assert len(briefs) == 2
    for brief in briefs:
        for required in ("Owner:", "Task:", "Start condition:", "Context you get:", "Done when:", "Return only:"):
            assert required in brief, required
    assert "bug-finder" in briefs[0] and "finding-verifier" in briefs[1]


def test_the_lead_skill_can_only_run_the_kit_cli():
    data, _ = parse(CLAUDE / "skills" / "pr-review" / "SKILL.md")
    bash_rules = re.findall(r"Bash\(([^)]*)\)", data["allowed-tools"])
    assert bash_rules == ["python3 -I ${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py review local-context *"]


def test_the_verify_skill_keeps_its_name_and_stays_invocable_by_claude():
    data, body = parse(CLAUDE / "skills" / "verify" / "SKILL.md")
    assert data["name"] == "verify"  # Claude Code runs a project skill with this name before commits
    assert data.get("disable-model-invocation") is not True
    assert "verify mark" in body and "checks run" in body


def test_schema_reference_matches_the_code():
    reference = json.loads((CLAUDE / "skills" / "pr-review" / "references" / "output-schema.json").read_text())
    assert reference == REVIEW_SCHEMA


def test_playbooks_use_the_same_severities_as_the_schema():
    severities = REVIEW_SCHEMA["properties"]["findings"]["items"]["properties"]["severity"]["enum"]
    for name in ("bug-finding", "finding-verification"):
        body = (CLAUDE / "skills" / name / "SKILL.md").read_text()
        for severity in severities:
            assert severity in body, f"{name}: {severity}"


def test_settings_fragment_wires_both_hooks_in_exec_form():
    settings = json.loads((CLAUDE / "settings.json").read_text())
    assert set(settings) == {"hooks"} and set(settings["hooks"]) == {"PreToolUse", "Stop"}
    [gate_group] = settings["hooks"]["PreToolUse"]
    [gate] = gate_group["hooks"]
    assert gate_group["matcher"] == "Bash" and gate["if"] == "Bash(git *)"
    [stop_group] = settings["hooks"]["Stop"]
    [stop] = stop_group["hooks"]
    for hook in (gate, stop):
        assert hook["type"] == "command" and hook["command"] == "python3"
        flag, script = hook["args"]
        assert flag == "-I" and script.startswith("${CLAUDE_PROJECT_DIR}/.claude/hooks/")
        assert (CLAUDE / "hooks" / script.rsplit("/", 1)[1]).is_file()
    # The hook must outlive the checks it runs, or a timeout would silently let the turn end.
    assert stop["timeout"] > Config().checks.timeout_seconds


def test_review_md_starter_has_the_sections_the_reviewer_relies_on():
    text = (KIT_ROOT / "REVIEW.md").read_text()
    for heading in (
        "## What Important means here",
        "## Evidence",
        "## Cap the nits",
        "## Do not report",
        "## Always check",
    ):
        assert heading in text
    assert len(text.encode()) < 150_000


def test_payload_files_stay_small_and_free_of_secrets():
    checked = 0
    for path in KIT_ROOT.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix not in (".py", ".md", ".yml", ".json"):
            continue
        text = path.read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 500, path
        assert redact_secrets(text) == text, f"{path} looks like it contains a secret"
        checked += 1
    assert checked > 30
