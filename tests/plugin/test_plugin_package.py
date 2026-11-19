"""The repository doubles as a Claude Code plugin whose one skill runs the installer."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
import yaml

from conftest import REPO_ROOT
from reviewkit import __version__

MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
SETUP_SKILL = REPO_ROOT / "skills" / "setup" / "SKILL.md"
NAME = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
# Marketplace names Claude Code refuses, or that would read as an official catalog.
RESERVED_WORDS = ("official", "anthropic", "claude", "marketplace", "plugins")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def skill():
    text = SETUP_SKILL.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    return yaml.safe_load(front), body


def test_manifest_names_the_plugin_and_tracks_the_library_version():
    manifest = load(MANIFEST)
    assert NAME.match(manifest["name"])
    assert manifest["version"] == __version__
    assert 20 <= len(manifest["description"]) <= 300
    # Only fields the manifest reference documents: an unknown key is ignored silently.
    assert set(manifest) <= {
        "name", "displayName", "version", "description", "author", "repository", "homepage", "keywords", "license",
    }  # fmt: skip


def test_pyproject_carries_the_same_version():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text


def test_marketplace_lists_the_plugin_at_the_repository_root():
    marketplace = load(MARKETPLACE)
    assert NAME.match(marketplace["name"])
    assert not any(word in marketplace["name"] for word in RESERVED_WORDS)
    assert marketplace["owner"]["name"]
    assert marketplace.get("description")
    [entry] = marketplace["plugins"]
    assert entry["name"] == load(MANIFEST)["name"]
    assert entry["source"] == "./"
    assert "version" not in entry  # plugin.json is the one place the version lives


def test_nothing_at_the_plugin_root_is_loaded_by_accident():
    # A root CLAUDE.md is not loaded by plugins and makes validation warn; hooks or MCP servers
    # at the root would be switched on for everyone who enables the plugin.
    for name in ("CLAUDE.md", "hooks", ".mcp.json", "agents", "commands", "bin"):
        assert not (REPO_ROOT / name).exists(), name
    assert [path.name for path in (REPO_ROOT / "skills").iterdir()] == ["setup"]


def test_the_setup_skill_is_started_by_a_person_and_previews_before_it_writes():
    front, body = skill()
    assert front["name"] == "setup"
    assert front["disable-model-invocation"] is True
    preview = body.index("--dry-run")
    install = body.index("## 3.")
    assert preview < install
    assert "${CLAUDE_PLUGIN_ROOT}/install.py" in body and "${CLAUDE_PROJECT_DIR}" in body
    assert "doctor" in body and "claude setup-token" in body


def test_the_setup_skill_only_pre_approves_commands_that_change_nothing():
    front, _ = skill()
    rules = re.findall(r"Bash\(([^)]*)\)", front["allowed-tools"])
    assert rules == [
        "python3 ${CLAUDE_PLUGIN_ROOT}/install.py --dry-run *",
        "python3 -I ${CLAUDE_PROJECT_DIR}/.claude/reviewkit/rk.py doctor *",
    ]


def test_the_setup_skill_never_asks_for_the_token_in_the_conversation():
    _, body = skill()
    assert "never paste" in body.lower()
    assert "ANTHROPIC_API_KEY" not in body


@pytest.mark.skipif(shutil.which("claude") is None, reason="the Claude Code CLI is not installed")
def test_claude_validates_the_plugin_and_the_marketplace():
    result = subprocess.run(
        ["claude", "plugin", "validate", str(REPO_ROOT)], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
