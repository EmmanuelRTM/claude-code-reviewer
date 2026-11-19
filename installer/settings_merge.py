"""Merge the kit's hooks into `.claude/settings.json`, and take them out again.

Pure functions over the file's text. The payload's `settings.json` is a
fragment, `{"hooks": {"<Event>": [<matcher group>, ...]}}`. Its groups are
appended to the target's events; every other key and hook is preserved.

A kit hook is recognised by the script it runs, not by its exact JSON. A user
who raises a timeout or widens a matcher has still got the kit's hook: a later
install must not append a second copy, and an uninstall must remove it, since
a hook whose script is gone cannot start and would block the session. A group
that names no script is compared by its canonical JSON instead.

A settings file that is not valid JSON, or whose shape is not what Claude Code
expects, is never rewritten: both functions raise `SettingsError` instead.
"""

from __future__ import annotations

import copy
import json
import re
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


# A word of a hook command that names a script file, e.g. `${CLAUDE_PROJECT_DIR}/.claude/hooks/gate.py`.
_SCRIPT = re.compile(r"[^\s\"']*/[^\s\"']+\.(?:py|sh|js|mjs|cjs|ts|rb|ps1)\Z")
_PATH_PREFIXES = ("${CLAUDE_PROJECT_DIR}/", "$CLAUDE_PROJECT_DIR/", "./")


def _identity(group: Any) -> str:
    return json.dumps(group, sort_keys=True)


def _words(hook: Any) -> list[str]:
    """Every word of a hook's command line, in shell form (`command`) or exec form (`args`)."""
    if not isinstance(hook, dict):
        return []
    command = hook.get("command")
    args = hook.get("args")
    words = command.split() if isinstance(command, str) else []
    return words + [str(arg) for arg in args] if isinstance(args, list) else words


def _hooks_of(group: Any) -> list[Any]:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return hooks if isinstance(hooks, list) else []


def _scripts(groups: Sequence[Any]) -> set[str]:
    """Project-relative script paths that the given groups run."""
    found: set[str] = set()
    for group in groups:
        for hook in _hooks_of(group):
            for word in _words(hook):
                word = word.strip("\"'")
                if not _SCRIPT.match(word):
                    continue
                for prefix in _PATH_PREFIXES:
                    if word.startswith(prefix):
                        word = word[len(prefix) :]
                found.add(word)
    return found


def _runs(hook: Any, scripts: set[str]) -> bool:
    return any(script in word for word in _words(hook) for script in scripts)


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
    group is already present. A group the user edited counts as present.
    """
    data = {} if existing is None else _load(existing)
    target = dict(data.get("hooks", {}))
    added = False
    for event, groups in hooks.items():
        current = list(target.get(event, []))
        present = {_identity(group) for group in current}
        for group in groups:
            scripts = _scripts([group])
            wired = bool(scripts) and any(_runs(hook, scripts) for have in current for hook in _hooks_of(have))
            if _identity(group) not in present and not wired:
                current.append(copy.deepcopy(group))
                present.add(_identity(group))
                added = True
                target[event] = current
    if not added:
        return SettingsChange(False)
    data["hooks"] = target
    return SettingsChange(True, dump_json(data))


def remove_hooks(existing: str, hooks: Mapping[str, Sequence[Any]]) -> SettingsChange:
    """Remove the fragment's groups, and any hook that runs one of the fragment's scripts.

    A group that also holds a hook of the user's keeps that hook. An event,
    and then `hooks` itself, is dropped when this leaves it empty.
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
        scripts = _scripts(groups)
        kept = []
        for group in target[event]:
            if _identity(group) in unwanted:
                continue
            own = [hook for hook in _hooks_of(group) if not _runs(hook, scripts)]
            if len(own) == len(_hooks_of(group)):
                kept.append(group)
            elif own:
                kept.append({**group, "hooks": own})
        if kept == target[event]:
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
