"""Read-only helpers shared by the install and uninstall planners."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from installer.fsops import Existing, inspect, relative_parts
from installer.kitfiles import decide_removal
from installer.layout import MANIFEST_PATH
from installer.manifest import Manifest, parse_manifest
from installer.payload import Ownership, PayloadFile, classify
from installer.plan import Action
from installer.settings_merge import Fragment, SettingsError, parse_fragment

UNTOUCHED = "it was left untouched"


def text_of(existing: Existing) -> Optional[str]:
    """The destination's text, or None when it does not exist. Raises `ValueError` if not UTF-8."""
    if existing.content is None:
        return None
    try:
        return existing.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("is not valid UTF-8 text") from exc


def present(target: Path, relative: str) -> bool:
    """Whether anything at all is at the path, including a symbolic link that leads nowhere."""
    return os.path.lexists(Path(target).joinpath(*relative_parts(relative)))


def reachable(target: Path, relative: str) -> bool:
    """Whether the path leads to something, following symbolic links."""
    return os.path.exists(Path(target).joinpath(*relative_parts(relative)))


def read_manifest(target: Path) -> tuple[Manifest, bool, list[str]]:
    """The target's manifest as `(Manifest, found, warnings)`.

    A missing manifest is an empty one. So is an unreadable one, with a
    warning: kit files that differ from the payload then count as locally
    modified, which is the safe way to be wrong.
    """
    existing = inspect(target, MANIFEST_PATH)
    if existing.content is None:
        return Manifest(), False, []  # a symlink in the way is reported when the manifest is written
    try:
        manifest, ignored = parse_manifest(existing.content.decode("utf-8"))
    except ValueError as exc:  # includes UnicodeDecodeError
        return Manifest(), True, [f"{MANIFEST_PATH} could not be read ({exc}); nothing in it was used"]
    return manifest, True, [f"{MANIFEST_PATH}: ignored the unsafe or malformed entry {path!r}" for path in ignored]


def plan_removals(
    target: Path, records: Mapping[str, str], *, force: bool, detail: str = ""
) -> tuple[list[Action], dict[str, str]]:
    """Plan the removal of kit files that the manifest recorded.

    Returns the actions and the records to keep, which are those of files left
    in place (modified locally and not forced, or not safely reachable). A
    recorded path that is not kit-owned is skipped without a word: a manifest
    must never get one of the user's files deleted.
    """
    actions: list[Action] = []
    kept: dict[str, str] = {}
    for path in sorted(records):
        if classify(path) is not Ownership.KIT:
            continue
        existing = inspect(target, path)
        if existing.problem:
            actions.append(Action.error(path, existing.problem))
            kept[path] = records[path]
            continue
        decision = decide_removal(existing.content, records[path], force=force)
        if decision is None:
            continue  # already gone
        if decision.delete:
            actions.append(Action.delete(path, detail))
        else:
            actions.append(Action.note(path, decision.status, detail))
            kept[path] = records[path]
    return actions, kept


def load_fragment(files: list[PayloadFile]) -> Optional[Fragment]:
    """The kit's settings fragment, or None when the payload ships none."""
    source: Optional[PayloadFile] = next((item for item in files if item.ownership is Ownership.SETTINGS), None)
    if source is None:
        return None
    try:
        return parse_fragment(source.source.read_bytes().decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SettingsError("the kit's settings fragment is not valid UTF-8") from exc
