"""Merge the kit's hooks into `.claude/settings.json`, and take them out again.

Pure functions over the file's text. The payload's `settings.json` is a
fragment, `{"hooks": {"<Event>": [<matcher group>, ...]}}`. Its groups are
appended to the target's events; every other key and hook is preserved. A
group is identified by its canonical JSON, so merging twice adds nothing and
removal takes out exactly what the fragment contains.

A settings file that is not valid JSON, or whose shape is not what Claude Code
expects, is never rewritten: both functions raise `SettingsError` instead.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from installer.errors import InstallError
from installer.jsonio import dump_json


class SettingsError(InstallError):
    """The target's settings file, or the kit's fragment, cannot be merged safely."""


@dataclass(frozen=True)
class Fragment:
    """The kit's hooks, plus any other top-level keys of the fragment (which are not merged)."""

    hooks: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    ignored_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SettingsChange:
    """Outcome of an edit: the new file content, or a request to delete the file."""

    changed: bool
    text: str = ""
    delete: bool = False


def _identity(group: Any) -> str:
    return json.dumps(group, sort_keys=True)


def _load(text: str) -> dict[str, Any]:
    """Parse settings text and check the parts this module relies on."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SettingsError(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SettingsError("the top level must be a JSON object")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError("`hooks` must be an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise SettingsError(f"`hooks.{event}` must be a list")
    return data


def parse_fragment(text: str) -> Fragment:
    """Validate the payload's settings fragment."""
    try:
        data = _load(text)
    except SettingsError as exc:
        raise SettingsError(f"the kit's settings fragment: {exc}") from exc
    hooks = data.get("hooks", {})
    for event, groups in hooks.items():
        if not all(isinstance(group, dict) for group in groups):
            raise SettingsError(f"the kit's settings fragment: every entry of `hooks.{event}` must be an object")
    return Fragment(hooks, tuple(sorted(key for key in data if key != "hooks")))


def merge_hooks(existing: Optional[str], hooks: Mapping[str, Sequence[Any]]) -> SettingsChange:
    """Append the fragment's groups that the target does not have yet.

    `existing` is the target file's text, or None when there is no file.
    Nothing is reported as changed, and the text is not reformatted, when every
    group is already present.
    """
    data = {} if existing is None else _load(existing)
    target = dict(data.get("hooks", {}))
    added = False
    for event, groups in hooks.items():
        current = list(target.get(event, []))
        present = {_identity(group) for group in current}
        for group in groups:
            if _identity(group) not in present:
                current.append(copy.deepcopy(group))
                present.add(_identity(group))
                added = True
                target[event] = current
    if not added:
        return SettingsChange(False)
    data["hooks"] = target
    return SettingsChange(True, dump_json(data))


def remove_hooks(existing: str, hooks: Mapping[str, Sequence[Any]]) -> SettingsChange:
    """Remove exactly the fragment's groups.

    An event, and then `hooks` itself, is dropped when this leaves it empty.
    When nothing at all is left the file should be deleted: it most likely
    existed only for the kit.
    """
    data = _load(existing)
    target = dict(data.get("hooks", {}))
    removed = False
    for event, groups in hooks.items():
        if event not in target:
            continue
        unwanted = {_identity(group) for group in groups}
        kept = [group for group in target[event] if _identity(group) not in unwanted]
        if len(kept) == len(target[event]):
            continue
        removed = True
        if kept:
            target[event] = kept
        else:
            del target[event]
    if not removed:
        return SettingsChange(False)
    if target:
        data["hooks"] = target
    else:
        del data["hooks"]
    if not data:
        return SettingsChange(True, delete=True)
    return SettingsChange(True, dump_json(data))
