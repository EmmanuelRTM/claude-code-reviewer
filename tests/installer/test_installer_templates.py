"""Template rendering and the values that fill the managed blocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from installer.blocks import HASH_MARKERS, HTML_MARKERS, upsert_block
from installer.errors import InstallError
from installer.kitlib import DEFAULT_BLOCKS_ROOT
from installer.templates import (
    BLOCKS,
    PROTECTED_PATHS,
    TemplateError,
    codeowners_rules,
    command_text,
    load_block,
    render_template,
    template_values,
)
from reviewkit.config import Config, parse_config

pytest_plugins = ("installer_fixtures",)


def config_with(**values) -> Config:
    return parse_config(values)[0]


# --- placeholders ------------------------------------------------------------


def test_render_replaces_every_occurrence() -> None:
    assert render_template("{{A}} and {{B}} and {{A}}", {"A": "x", "B": "y"}) == "x and y and x"


def test_render_without_placeholders_returns_the_text() -> None:
    assert render_template("plain\ntext\n", {}) == "plain\ntext\n"


def test_an_unknown_placeholder_is_an_error() -> None:
    with pytest.raises(TemplateError, match=r"\{\{MISSING\}\}"):
        render_template("{{KNOWN}} {{MISSING}}", {"KNOWN": "x"})


def test_values_are_inserted_verbatim_and_not_rescanned() -> None:
    assert render_template("run {{CMD}}", {"CMD": "echo {{NOT_A_PLACEHOLDER}}"}) == "run echo {{NOT_A_PLACEHOLDER}}"


def test_other_brace_syntax_is_left_alone() -> None:
    text = "token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }} and {{ spaced }} and {single}"
    assert render_template(text, {}) == text


def test_multi_line_values() -> None:
    assert render_template("a\n{{RULES}}\nz\n", {"RULES": "one\ntwo"}) == "a\none\ntwo\nz\n"


# --- commands ----------------------------------------------------------------


def test_a_configured_command_is_shown_as_code() -> None:
    assert command_text("npm run test", "commands.test") == "`npm run test`"
    assert command_text("  make lint ", "commands.lint") == "`make lint`"


@pytest.mark.parametrize("command", ["", "   ", "\t"])
def test_a_missing_command_says_how_to_configure_it(command: str) -> None:
    assert command_text(command, "commands.test") == "not configured (set commands.test in .claude/reviewkit.json)"
    assert command_text(command, "commands.build") == "not configured (set commands.build in .claude/reviewkit.json)"


def test_a_command_with_backticks_stays_a_valid_code_span() -> None:
    assert command_text("echo `date`", "commands.test") == "`` echo `date` ``"
    assert command_text("a ``b`` c", "commands.test") == "```a ``b`` c```"


def test_a_multi_line_command_is_referenced_instead_of_shown() -> None:
    assert command_text("make a\nmake b", "commands.test") == "see commands.test in .claude/reviewkit.json"


def test_template_values_come_from_the_configuration() -> None:
    config = config_with(commands={"test": "npm run test", "build": "npm run build"}, owners=["@org/leads"])
    values = template_values(config)
    assert values["TEST_COMMAND"] == "`npm run test`"
    assert values["LINT_COMMAND"] == "not configured (set commands.lint in .claude/reviewkit.json)"
    assert values["BUILD_COMMAND"] == "`npm run build`"
    assert values["CODEOWNERS_RULES"] == codeowners_rules(("@org/leads",))


# --- code owners -------------------------------------------------------------


def test_protected_paths_and_their_order() -> None:
    assert PROTECTED_PATHS == ("/REVIEW.md", "/docs/reliability-checklist.md", "/CLAUDE.md", "/.claude/", "/.github/")


def test_rules_with_owners_are_aligned() -> None:
    assert codeowners_rules(("@org/leads", "@alice")).splitlines() == [
        "/REVIEW.md                      @org/leads @alice",
        "/docs/reliability-checklist.md  @org/leads @alice",
        "/CLAUDE.md                      @org/leads @alice",
        "/.claude/                       @org/leads @alice",
        "/.github/                       @org/leads @alice",
    ]


def test_rules_without_owners_are_commented_out() -> None:
    lines = codeowners_rules(()).splitlines()
    assert lines == [
        "# /REVIEW.md                      @your-team",
        "# /docs/reliability-checklist.md  @your-team",
        "# /CLAUDE.md                      @your-team",
        "# /.claude/                       @your-team",
        "# /.github/                       @your-team",
    ]


@pytest.mark.parametrize("owner", ["@alice", "@org/team-name", "@org/team.name_2", "dev@example.com"])
def test_owner_forms_github_accepts(owner: str) -> None:
    assert owner in codeowners_rules((owner,))


@pytest.mark.parametrize("owner", ["alice", "@", "@a b", "@alice\n/src/ @mallory", "@a#b", "", "@@a"])
def test_an_invalid_owner_is_rejected_by_key_name(owner: str) -> None:
    with pytest.raises(InstallError, match="owners"):
        codeowners_rules(("@fine", owner))


# --- block definitions ---------------------------------------------------------


def test_the_three_managed_blocks() -> None:
    by_template = {spec.template: spec for spec in BLOCKS}
    assert set(by_template) == {"CLAUDE.md.block", "CODEOWNERS.block", "gitignore.block"}
    assert by_template["CLAUDE.md.block"].markers == HTML_MARKERS
    assert by_template["CLAUDE.md.block"].candidates == ("CLAUDE.md",)
    assert by_template["CODEOWNERS.block"].markers == HASH_MARKERS
    assert by_template["CODEOWNERS.block"].candidates[0] == ".github/CODEOWNERS"
    assert by_template["gitignore.block"].markers == HASH_MARKERS
    assert by_template["gitignore.block"].candidates == (".gitignore",)


def test_codeowners_goes_to_the_file_github_actually_reads() -> None:
    spec = next(spec for spec in BLOCKS if spec.template == "CODEOWNERS.block")
    # No CODEOWNERS anywhere: create the conventional one.
    assert spec.destination(lambda path: False) == ".github/CODEOWNERS"
    # GitHub uses the first of .github/, the root and docs/. A new .github/CODEOWNERS
    # would silently replace an existing one elsewhere, so the block joins that file.
    assert spec.destination(lambda path: path == "CODEOWNERS") == "CODEOWNERS"
    assert spec.destination(lambda path: path == "docs/CODEOWNERS") == "docs/CODEOWNERS"
    assert spec.destination(lambda path: path in ("CODEOWNERS", "docs/CODEOWNERS")) == "CODEOWNERS"
    assert spec.destination(lambda path: True) == ".github/CODEOWNERS"


def test_load_block_reads_and_renders(kit) -> None:
    spec = next(spec for spec in BLOCKS if spec.template == "CLAUDE.md.block")
    text = load_block(kit.blocks, spec, template_values(config_with(commands={"test": "pytest -q"})))
    assert "- Test: `pytest -q`" in text
    assert "- Lint: not configured (set commands.lint in .claude/reviewkit.json)" in text
    assert "{{" not in text


def test_load_block_reports_a_missing_template(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="CLAUDE.md.block"):
        load_block(tmp_path, BLOCKS[0], {})


def test_load_block_names_the_template_with_an_unknown_placeholder(kit) -> None:
    kit.write_block("gitignore.block", "# reviewkit:begin\n{{SURPRISE}}\n# reviewkit:end\n")
    spec = next(spec for spec in BLOCKS if spec.template == "gitignore.block")
    with pytest.raises(TemplateError, match=r"gitignore\.block.*SURPRISE"):
        load_block(kit.blocks, spec, template_values(Config()))


# --- the real templates shipped in blocks/ -------------------------------------


@pytest.mark.parametrize("spec", BLOCKS, ids=lambda spec: spec.template)
def test_shipped_templates_render_into_a_single_block(spec) -> None:
    for config in (Config(), config_with(commands={"test": "npm run test"}, owners=["@org/leads", "@alice"])):
        rendered = load_block(DEFAULT_BLOCKS_ROOT, spec, template_values(config))
        content = upsert_block(None, rendered, spec.markers)
        assert content.splitlines()[0] == spec.markers.begin
        assert content.splitlines()[-1] == spec.markers.end
        assert "{{" not in content


def test_shipped_claude_block_shows_the_commands() -> None:
    spec = next(spec for spec in BLOCKS if spec.template == "CLAUDE.md.block")
    config = config_with(commands={"test": "npm run test", "lint": "npm run lint"})
    rendered = load_block(DEFAULT_BLOCKS_ROOT, spec, template_values(config))
    assert "- Test: `npm run test`" in rendered
    assert "- Lint: `npm run lint`" in rendered
    assert "- Build: not configured (set commands.build in .claude/reviewkit.json)" in rendered
