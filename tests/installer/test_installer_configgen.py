"""Generating `.claude/reviewkit.json`: `--set` coercion, layering and existing files."""

from __future__ import annotations

import json

import pytest

from installer.configgen import deep_merge, parse_assignments, plan_config
from installer.errors import InstallError
from installer.report import CREATED, UNCHANGED, UPDATED
from reviewkit.config import Config, config_to_dict

DEFAULTS = config_to_dict(Config())


# --- `--set KEY=VALUE` -----------------------------------------------------------


def test_strings_are_taken_as_they_are() -> None:
    assert parse_assignments(["commands.test=npm test -- --ci"]) == {"commands": {"test": "npm test -- --ci"}}
    assert parse_assignments(["commands.test=CI=1 make test"]) == {"commands": {"test": "CI=1 make test"}}
    assert parse_assignments(["gate.mode=enforce"]) == {"gate": {"mode": "enforce"}}
    assert parse_assignments(["commands.lint= spaced "]) == {"commands": {"lint": " spaced "}}
    assert parse_assignments(["commands.build="]) == {"commands": {"build": ""}}


def test_integers_are_parsed() -> None:
    assert parse_assignments(["checks.timeout_seconds=900"]) == {"checks": {"timeout_seconds": 900}}
    assert parse_assignments(["review.max_rounds= 5 "]) == {"review": {"max_rounds": 5}}
    assert parse_assignments(["gate.max_blocks=-1"]) == {"gate": {"max_blocks": -1}}
    value = parse_assignments(["version=2"])["version"]
    assert value == 2 and type(value) is int


def test_booleans_accept_true_and_false() -> None:
    assert parse_assignments(["gate.require_trailer=false"]) == {"gate": {"require_trailer": False}}
    assert parse_assignments(["checks.zero_run_is_failure=TRUE"]) == {"checks": {"zero_run_is_failure": True}}
    assert parse_assignments(["review.skip_bot_authors= False "]) == {"review": {"skip_bot_authors": False}}


def test_lists_split_on_commas() -> None:
    assert parse_assignments(["owners=@org/leads,@alice"]) == {"owners": ["@org/leads", "@alice"]}
    assert parse_assignments(["owners= @org/leads , @alice "]) == {"owners": ["@org/leads", "@alice"]}
    assert parse_assignments(["checks.on_stop=lint,test"]) == {"checks": {"on_stop": ["lint", "test"]}}
    assert parse_assignments(["owners=a,,b,"]) == {"owners": ["a", "b"]}


@pytest.mark.parametrize("empty", ["owners=", "owners=  ", "owners=,"])
def test_an_empty_list_value_gives_an_empty_list(empty: str) -> None:
    assert parse_assignments([empty]) == {"owners": []}


def test_nested_keys() -> None:
    assert parse_assignments(["review.models.lead=sonnet"]) == {"review": {"models": {"lead": "sonnet"}}}
    assert parse_assignments(["risk.approvals.high=3"]) == {"risk": {"approvals": {"high": 3}}}


def test_assignments_combine_and_the_last_one_wins() -> None:
    overrides = parse_assignments(["commands.test=a", "commands.lint=b", "commands.test=c", "owners=@x"])
    assert overrides == {"commands": {"test": "c", "lint": "b"}, "owners": ["@x"]}


@pytest.mark.parametrize("key", ["commands.tset", "nope", "gate.mode.extra", "review.models.leader", "commands."])
def test_unknown_keys_are_rejected_by_name(key: str) -> None:
    with pytest.raises(InstallError) as caught:
        parse_assignments([f"{key}=x"])
    assert key in str(caught.value)


def test_an_unknown_key_lists_the_valid_ones() -> None:
    with pytest.raises(InstallError, match="health_check"):
        parse_assignments(["commands.tset=x"])


@pytest.mark.parametrize("key", ["commands", "review.models", "risk"])
def test_a_section_is_not_a_setting(key: str) -> None:
    with pytest.raises(InstallError, match="section"):
        parse_assignments([f"{key}=x"])


@pytest.mark.parametrize(
    "assignment",
    [
        "checks.timeout_seconds=soon",
        "checks.timeout_seconds=1.5",
        "checks.timeout_seconds=",
        "checks.timeout_seconds=1_000",
        "version=true",
        "gate.require_trailer=maybe",
        "gate.require_trailer=1",
        "gate.require_trailer=",
    ],
)
def test_bad_values_are_rejected_by_key_name(assignment: str) -> None:
    key = assignment.split("=")[0]
    with pytest.raises(InstallError) as caught:
        parse_assignments([assignment])
    assert key in str(caught.value)


@pytest.mark.parametrize("assignment", ["novalue", "=x", " =x", ""])
def test_an_assignment_needs_a_key_and_an_equals_sign(assignment: str) -> None:
    with pytest.raises(InstallError, match="KEY=VALUE"):
        parse_assignments([assignment])


# --- deep merge --------------------------------------------------------------------


def test_deep_merge_overlays_nested_keys_and_replaces_lists() -> None:
    base = {"a": {"x": 1, "y": [1, 2]}, "b": "keep", "c": [1]}
    overlay = {"a": {"y": [3], "z": True}, "c": [], "d": {"new": 1}}
    assert deep_merge(base, overlay) == {"a": {"x": 1, "y": [3], "z": True}, "b": "keep", "c": [], "d": {"new": 1}}


def test_deep_merge_leaves_its_inputs_alone() -> None:
    base = {"a": {"x": [1]}}
    overlay = {"a": {"y": [2]}}
    merged = deep_merge(base, overlay)
    merged["a"]["x"].append(9)
    merged["a"]["y"].append(9)
    assert base == {"a": {"x": [1]}} and overlay == {"a": {"y": [2]}}


# --- a repository without a configuration ------------------------------------------


def test_absent_configuration_starts_from_the_defaults() -> None:
    plan = plan_config(None, detected={}, overrides={})
    assert plan.status == CREATED
    assert plan.text is not None
    assert json.loads(plan.text) == DEFAULTS
    assert plan.text == json.dumps(DEFAULTS, indent=2) + "\n"
    assert plan.config == Config()
    assert plan.warnings == ()


def test_detection_then_overrides_are_layered_on_the_defaults() -> None:
    detected = {"commands": {"test": "npm run test", "lint": "npm run lint"}, "default_branch": "trunk"}
    overrides = {"commands": {"test": "make test"}, "owners": ["@org/leads"]}
    plan = plan_config(None, detected=detected, overrides=overrides)
    data = json.loads(plan.text or "")
    assert data["commands"]["test"] == "make test"  # explicit beats detected
    assert data["commands"]["lint"] == "npm run lint"  # detected beats default
    assert data["commands"]["build"] == ""  # default
    assert data["default_branch"] == "trunk"
    assert data["owners"] == ["@org/leads"]
    assert plan.config.commands.test == "make test"
    assert plan.config.owners == ("@org/leads",)


def test_an_explicit_empty_value_beats_detection() -> None:
    plan = plan_config(None, detected={"commands": {"test": "npm run test"}}, overrides={"commands": {"test": ""}})
    assert plan.config.commands.test == ""


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"gate": {"mode": "bogus"}}, "gate.mode"),
        ({"checks": {"timeout_seconds": "soon"}}, "checks.timeout_seconds"),
        ({"checks": {"on_stop": ["deploy"]}}, "checks.on_stop"),
        ({"review": {"max_rounds": 0}}, "review.max_rounds"),
        ({"commands": "npm test"}, "commands"),
    ],
)
def test_an_invalid_result_is_rejected_by_key_name(overrides: dict, key: str) -> None:
    with pytest.raises(InstallError, match=key):
        plan_config(None, detected={}, overrides=overrides)


def test_unknown_keys_pass_through_with_a_warning() -> None:
    plan = plan_config(None, detected={}, overrides={"mystery": 1})
    assert json.loads(plan.text or "")["mystery"] == 1
    assert any("mystery" in warning for warning in plan.warnings)


# --- a repository that already has one ----------------------------------------------

EXISTING = '{\n    "default_branch": "develop",\n    "commands": {"test": "mine"},\n    "note": "keep me"\n}\n'


def test_an_existing_configuration_is_left_alone() -> None:
    plan = plan_config(EXISTING, detected={}, overrides={})
    assert plan.status == UNCHANGED
    assert plan.text is None
    assert plan.config.default_branch == "develop"
    assert plan.config.commands.test == "mine"


def test_detection_never_touches_an_existing_configuration() -> None:
    detected = {"commands": {"test": "npm run test", "lint": "npm run lint"}, "default_branch": "trunk"}
    plan = plan_config(EXISTING, detected=detected, overrides={})
    assert plan.status == UNCHANGED
    assert plan.config.commands.test == "mine"
    assert plan.config.commands.lint == ""
    assert plan.config.default_branch == "develop"


def test_only_the_overridden_keys_change() -> None:
    plan = plan_config(EXISTING, detected={}, overrides={"gate": {"mode": "enforce"}, "commands": {"lint": "ruff"}})
    assert plan.status == UPDATED
    data = json.loads(plan.text or "")
    assert data == {
        "default_branch": "develop",
        "commands": {"test": "mine", "lint": "ruff"},
        "note": "keep me",
        "gate": {"mode": "enforce"},
    }
    assert list(data) == ["default_branch", "commands", "note", "gate"]  # no reordering, no defaults filled in
    assert plan.text == json.dumps(data, indent=2) + "\n"
    assert plan.config.gate.mode == "enforce"


def test_an_override_equal_to_the_current_value_is_not_a_change() -> None:
    plan = plan_config(EXISTING, detected={}, overrides={"commands": {"test": "mine"}, "default_branch": "develop"})
    assert plan.status == UNCHANGED
    assert plan.text is None


@pytest.mark.parametrize("corrupt", ["", "{not json", "[]", '"text"', "null", "// comment\n{}"])
def test_a_corrupt_existing_configuration_is_an_error(corrupt: str) -> None:
    for overrides in ({}, {"gate": {"mode": "enforce"}}):
        with pytest.raises(InstallError, match=r"\.claude/reviewkit\.json"):
            plan_config(corrupt, detected={}, overrides=overrides)


def test_an_invalid_existing_value_is_an_error() -> None:
    with pytest.raises(InstallError, match="gate.mode"):
        plan_config('{"gate": {"mode": "bogus"}}', detected={}, overrides={})
    with pytest.raises(InstallError, match="checks.max_blocks"):
        plan_config('{"checks": {"max_blocks": "three"}}', detected={}, overrides={"gate": {"mode": "off"}})


def test_an_override_can_repair_an_invalid_value() -> None:
    plan = plan_config('{"gate": {"mode": "bogus"}}', detected={}, overrides={"gate": {"mode": "enforce"}})
    assert plan.status == UPDATED
    assert json.loads(plan.text or "") == {"gate": {"mode": "enforce"}}


def test_unknown_keys_in_an_existing_file_are_kept_and_reported() -> None:
    plan = plan_config(EXISTING, detected={}, overrides={})
    assert any("note" in warning for warning in plan.warnings)
