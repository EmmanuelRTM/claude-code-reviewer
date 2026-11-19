"""Merging the kit's hooks into `.claude/settings.json`, and taking them out again."""

from __future__ import annotations

import json

import pytest

from installer.jsonio import dump_json
from installer.settings_merge import SettingsError, merge_hooks, parse_fragment, remove_hooks

GATE = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 -I .claude/hooks/gate.py"}]}
STOP = {"hooks": [{"type": "command", "command": "python3 -I .claude/hooks/stop.py", "timeout": 900}]}
HOOKS = {"PreToolUse": [GATE], "Stop": [STOP]}

USER_GROUP = {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "./scripts/format.sh"}]}
OTHER_GROUP = {"matcher": "*", "hooks": [{"type": "command", "command": "echo done"}]}
USER_SETTINGS = {
    "permissions": {"allow": ["Bash(npm test)"], "deny": ["Read(./.env)"]},
    "hooks": {"PreToolUse": [USER_GROUP], "PostToolUse": [OTHER_GROUP]},
    "env": {"TEAM": "café"},
}


# --- output format -------------------------------------------------------------


def test_dump_json_is_two_space_indented_with_a_trailing_newline() -> None:
    assert dump_json({"a": {"b": [1, 2]}}) == '{\n  "a": {\n    "b": [\n      1,\n      2\n    ]\n  }\n}\n'


def test_dump_json_keeps_key_order_and_readable_text() -> None:
    text = dump_json({"z": 1, "a": "café"})
    assert text.index('"z"') < text.index('"a"')
    assert "café" in text


def test_dump_json_survives_text_that_utf8_cannot_encode() -> None:
    text = dump_json({"odd": "\ud800"})
    text.encode("utf-8")  # must not raise
    assert json.loads(text) == {"odd": "\ud800"}


# --- the kit's fragment --------------------------------------------------------


def test_parse_fragment() -> None:
    fragment = parse_fragment(json.dumps({"hooks": HOOKS}))
    assert fragment.hooks == HOOKS
    assert fragment.ignored_keys == ()


def test_parse_fragment_reports_keys_it_does_not_merge() -> None:
    fragment = parse_fragment(json.dumps({"hooks": HOOKS, "permissions": {}, "$schema": "x"}))
    assert fragment.ignored_keys == ("$schema", "permissions")


@pytest.mark.parametrize(
    "text",
    ["", "nope", "[]", '{"hooks": []}', '{"hooks": {"Stop": {}}}', '{"hooks": {"Stop": ["not a group"]}}'],
)
def test_parse_fragment_rejects_a_malformed_fragment(text: str) -> None:
    with pytest.raises(SettingsError):
        parse_fragment(text)


# --- merge -----------------------------------------------------------------------


def test_an_absent_file_gets_the_fragment() -> None:
    change = merge_hooks(None, HOOKS)
    assert change.changed
    assert change.text == json.dumps({"hooks": HOOKS}, indent=2) + "\n"


def test_existing_keys_and_hooks_are_preserved() -> None:
    change = merge_hooks(json.dumps(USER_SETTINGS), HOOKS)
    assert change.changed
    merged = json.loads(change.text)
    assert merged["permissions"] == USER_SETTINGS["permissions"]
    assert merged["env"] == USER_SETTINGS["env"]
    assert merged["hooks"]["PreToolUse"] == [USER_GROUP, GATE]  # appended after the user's own
    assert merged["hooks"]["PostToolUse"] == [OTHER_GROUP]
    assert merged["hooks"]["Stop"] == [STOP]
    assert list(merged) == ["permissions", "hooks", "env"]
    assert "café" in change.text


def test_a_file_without_hooks_gains_them() -> None:
    change = merge_hooks('{"model": "opus"}', HOOKS)
    assert json.loads(change.text) == {"model": "opus", "hooks": HOOKS}


def test_merging_twice_adds_nothing() -> None:
    first = merge_hooks(json.dumps(USER_SETTINGS), HOOKS)
    second = merge_hooks(first.text, HOOKS)
    assert second.changed is False


def test_a_group_already_present_is_recognised_whatever_its_key_order() -> None:
    reordered = {"hooks": [{"command": "python3 -I .claude/hooks/gate.py", "type": "command"}], "matcher": "Bash"}
    change = merge_hooks(json.dumps({"hooks": {"PreToolUse": [reordered]}}), HOOKS)
    merged = json.loads(change.text)
    assert merged["hooks"]["PreToolUse"] == [reordered]
    assert merged["hooks"]["Stop"] == [STOP]


def test_a_complete_file_is_left_alone_whatever_its_formatting() -> None:
    oddly_formatted = json.dumps({"hooks": HOOKS}, indent=8, sort_keys=True)
    assert merge_hooks(oddly_formatted, HOOKS).changed is False


def test_a_similar_but_different_group_is_not_a_duplicate() -> None:
    similar = dict(GATE, matcher="Bash|Edit")
    merged = json.loads(merge_hooks(json.dumps({"hooks": {"PreToolUse": [similar]}}), HOOKS).text)
    assert merged["hooks"]["PreToolUse"] == [similar, GATE]


def test_an_empty_fragment_changes_nothing() -> None:
    assert merge_hooks(None, {}).changed is False
    assert merge_hooks(None, {"Stop": []}).changed is False
    assert merge_hooks('{"a": 1}', {"Stop": []}).changed is False


def test_merge_does_not_modify_its_inputs() -> None:
    hooks = json.loads(json.dumps(HOOKS))
    merged = json.loads(merge_hooks(None, hooks).text)
    merged["hooks"]["Stop"][0]["hooks"].append("x")
    assert hooks == HOOKS


@pytest.mark.parametrize(
    "corrupt",
    [
        "",
        "{not json",
        '{"hooks": {"PreToolUse": [}',
        "[]",
        '"just a string"',
        "null",
        '{"hooks": []}',
        '{"hooks": "x"}',
        '{"hooks": {"PreToolUse": {}}}',
        '{"hooks": {"Stop": "x"}}',
        '{"hooks": {"SomeOtherEvent": 5}}',
        "// a comment\n{}",
    ],
)
def test_a_corrupt_or_wrongly_typed_file_is_refused(corrupt: str) -> None:
    with pytest.raises(SettingsError):
        merge_hooks(corrupt, HOOKS)
    with pytest.raises(SettingsError):
        remove_hooks(corrupt, HOOKS)


# --- removal ----------------------------------------------------------------------


def test_removal_takes_out_only_the_kit_groups() -> None:
    installed = merge_hooks(json.dumps(USER_SETTINGS), HOOKS).text
    change = remove_hooks(installed, HOOKS)
    assert change.changed and not change.delete
    assert json.loads(change.text) == USER_SETTINGS  # `Stop` went away with its last group


def test_removal_drops_the_hooks_key_when_it_becomes_empty() -> None:
    change = remove_hooks(json.dumps({"model": "opus", "hooks": HOOKS}), HOOKS)
    assert json.loads(change.text) == {"model": "opus"}


def test_removing_everything_asks_for_the_file_to_be_deleted() -> None:
    change = remove_hooks(json.dumps({"hooks": HOOKS}), HOOKS)
    assert change.changed and change.delete


def test_removal_without_kit_groups_changes_nothing() -> None:
    assert remove_hooks(json.dumps(USER_SETTINGS), HOOKS).changed is False
    assert remove_hooks("{}", HOOKS).changed is False  # an empty file that is not ours stays
    assert remove_hooks('{"hooks": {}}', HOOKS).changed is False


def test_removal_keeps_lists_that_were_already_empty() -> None:
    existing = {"hooks": {"Stop": [STOP], "Notification": []}}
    change = remove_hooks(json.dumps(existing), HOOKS)
    assert json.loads(change.text) == {"hooks": {"Notification": []}}


def test_removal_takes_out_every_copy_of_a_kit_group() -> None:
    existing = {"hooks": {"PreToolUse": [GATE, USER_GROUP, GATE]}}
    assert json.loads(remove_hooks(json.dumps(existing), HOOKS).text) == {"hooks": {"PreToolUse": [USER_GROUP]}}
