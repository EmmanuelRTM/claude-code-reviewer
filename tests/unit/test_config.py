"""Configuration loading: defaults, overrides, validation and environment switches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reviewkit.config import (
    CONFIG_RELATIVE_PATH,
    Config,
    ConfigError,
    config_to_dict,
    effective_gate_mode,
    hooks_disabled,
    load_config,
    parse_config,
)


def write_config(project: Path, data: object) -> Path:
    path = project / CONFIG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_defaults_when_no_file(tmp_path: Path) -> None:
    loaded = load_config(tmp_path)
    assert loaded.source is None
    assert loaded.warnings == ()
    cfg = loaded.config
    assert cfg.gate.mode == "observe"
    assert cfg.gate.max_blocks == 2
    assert cfg.checks.max_blocks == 3
    assert cfg.review.level == "standard"
    assert cfg.review.max_rounds == 3
    assert cfg.review.publish == "advisory"
    assert cfg.review.trigger == "every_push"
    assert cfg.risk.approvals.high == 2
    assert cfg.risk.approvals.low == 0


def test_partial_file_overrides_only_what_it_names(tmp_path: Path) -> None:
    write_config(tmp_path, {"commands": {"test": "pytest -q"}, "gate": {"mode": "enforce"}})
    cfg = load_config(tmp_path).config
    assert cfg.commands.test == "pytest -q"
    assert cfg.commands.lint == ""
    assert cfg.gate.mode == "enforce"
    assert cfg.gate.max_blocks == 2


def test_lists_become_tuples_and_nested_sections_load(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        {
            "checks": {"on_stop": ["lint", "test"]},
            "review": {"models": {"lead": "sonnet"}},
            "risk": {"high_paths": ["**/auth/**"], "approvals": {"high": 3}},
        },
    )
    cfg = load_config(tmp_path).config
    assert cfg.checks.on_stop == ("lint", "test")
    assert cfg.review.models.lead == "sonnet"
    assert cfg.review.models.verifier == "opus"
    assert cfg.risk.high_paths == ("**/auth/**",)
    assert cfg.risk.approvals.high == 3
    assert cfg.risk.approvals.medium == 1


def test_unknown_keys_are_warnings_not_errors(tmp_path: Path) -> None:
    write_config(tmp_path, {"gate": {"mode": "observe", "typo_key": 1}, "extra": {}})
    loaded = load_config(tmp_path)
    assert loaded.config.gate.mode == "observe"
    assert any("gate.typo_key" in w for w in loaded.warnings)
    assert any("extra" in w for w in loaded.warnings)


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ({"gate": {"mode": "strict"}}, "gate.mode"),
        ({"review": {"level": "extreme"}}, "review.level"),
        ({"review": {"publish": "loud"}}, "review.publish"),
        ({"review": {"trigger": "sometimes"}}, "review.trigger"),
        ({"review": {"max_rounds": 0}}, "review.max_rounds"),
        ({"checks": {"timeout_seconds": -1}}, "checks.timeout_seconds"),
        ({"checks": {"on_stop": ["deploy"]}}, "checks.on_stop"),
        ({"gate": {"max_blocks": "two"}}, "gate.max_blocks"),
        ({"commands": {"test": 5}}, "commands.test"),
        ({"risk": {"approvals": {"high": -1}}}, "risk.approvals.high"),
        ({"gate": []}, "gate"),
    ],
)
def test_invalid_values_raise_with_the_field_name(tmp_path: Path, data: dict, needle: str) -> None:
    write_config(tmp_path, data)
    with pytest.raises(ConfigError) as err:
        load_config(tmp_path)
    assert needle in str(err.value)


def test_only_the_lower_risk_tiers_can_be_skipped() -> None:
    cfg, _ = parse_config({"review": {"skip_risk_tiers": ["low", "medium"]}})
    assert cfg.review.skip_risk_tiers == ("low", "medium")
    for bad in (["high"], ["tiny"], "low"):
        with pytest.raises(ConfigError, match="skip_risk_tiers"):
            parse_config({"review": {"skip_risk_tiers": bad}})


def test_author_approval_is_on_by_default_and_must_be_a_boolean() -> None:
    assert parse_config({})[0].risk.author_can_approve is True
    assert parse_config({"risk": {"author_can_approve": False}})[0].risk.author_can_approve is False
    with pytest.raises(ConfigError, match="risk.author_can_approve"):
        parse_config({"risk": {"author_can_approve": "no"}})


def test_invalid_json_raises(tmp_path: Path) -> None:
    write_config(tmp_path, "{not json")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_top_level_must_be_an_object() -> None:
    with pytest.raises(ConfigError):
        parse_config([1, 2, 3])


def test_booleans_are_not_accepted_as_integers() -> None:
    with pytest.raises(ConfigError):
        parse_config({"gate": {"max_blocks": True}})


def test_round_trip_through_dict() -> None:
    cfg = Config()
    again, warnings = parse_config(config_to_dict(cfg))
    assert again == cfg
    assert warnings == ()
    # The serialized form is plain JSON.
    json.dumps(config_to_dict(cfg))


def test_gate_mode_environment_override() -> None:
    cfg = Config()
    assert effective_gate_mode(cfg, {}) == "observe"
    assert effective_gate_mode(cfg, {"VERIFY_GATE_MODE": "enforce"}) == "enforce"
    assert effective_gate_mode(cfg, {"VERIFY_GATE_MODE": "OFF"}) == "off"
    # An unrecognised value never loosens or tightens the committed setting.
    assert effective_gate_mode(cfg, {"VERIFY_GATE_MODE": "banana"}) == "observe"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"REVIEWKIT_DISABLE": "1"}, True),
        ({"REVIEWKIT_DISABLE": "0"}, False),
        ({"GITHUB_ACTIONS": "true"}, True),
        ({"CI": "true"}, True),
        ({"CI": "true", "REVIEWKIT_HOOKS_IN_CI": "1"}, False),
    ],
)
def test_hooks_disabled(env: dict, expected: bool) -> None:
    assert hooks_disabled(env) is expected
