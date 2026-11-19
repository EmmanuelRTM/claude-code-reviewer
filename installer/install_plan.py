"""Plan an install or upgrade: read the target, decide every path, write nothing.

Two kinds of problem are told apart. One that makes the whole run meaningless
(an invalid configuration, a broken kit) raises `InstallError` before anything
is decided. One that concerns a single path (a symbolic link in the way, a
settings file that is not JSON, damaged block markers) becomes an error entry
for that path: the file is left untouched and the rest of the plan stands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from installer.blocks import BlockError, Markers, upsert_block
from installer.configgen import ConfigPlan, plan_config
from installer.detect import detect_settings
from installer.errors import InstallError
from installer.fsops import inspect
from installer.kitfiles import decide_kit_file
from installer.layout import CONFIG_PATH, HOOK_RUNTIME, MANIFEST_PATH, SETTINGS_PATH
from installer.manifest import Manifest, render_manifest, same_content
from installer.payload import Ownership, PayloadFile, kit_version, scan_payload
from installer.plan import Action, Plan
from installer.planning import (
    UNTOUCHED,
    load_fragment,
    plan_removals,
    present,
    reachable,
    read_manifest,
    text_of,
)
from installer.report import CREATED, ERROR, KEPT, MERGED, UNCHANGED, UPDATED
from installer.settings_merge import SettingsError, merge_hooks
from installer.templates import BLOCKS, load_block, template_values

_STALE = "no longer part of the kit"
_NO_OWNERS = (
    "code owners are not set: the CODEOWNERS rules were written commented out. "
    f"Set `owners` in {CONFIG_PATH} (or pass --set owners=@org/team) and run the installer again."
)


def _kit_file(target: Path, item: PayloadFile, recorded: Optional[str], force: bool) -> tuple[Action, Optional[str]]:
    """Plan one kit-owned file; returns the action and the hash the manifest should keep."""
    existing = inspect(target, item.path)
    if existing.problem:
        return Action.error(item.path, existing.problem), recorded
    payload = item.source.read_bytes()
    decision = decide_kit_file(payload, existing.content, recorded, force=force)
    if decision.write:
        action = Action.write(item.path, decision.status, payload, executable=item.executable)
        return action, decision.recorded
    return Action.note(item.path, decision.status), decision.recorded


def _user_file(target: Path, item: PayloadFile) -> Action:
    """Plan one create-if-absent file: written once, never again."""
    existing = inspect(target, item.path)
    if existing.exists or (existing.problem and reachable(target, item.path)):
        return Action.note(item.path, KEPT)  # the user has something here; nothing is written
    if existing.problem:
        return Action.error(item.path, existing.problem)
    return Action.write(item.path, CREATED, item.source.read_bytes(), executable=item.executable)


def _configuration(target: Path, overrides: Mapping[str, Any], detect: bool) -> tuple[Action, ConfigPlan]:
    """Plan `.claude/reviewkit.json`. Any problem here is fatal: the blocks depend on it."""
    existing = inspect(target, CONFIG_PATH)
    if existing.problem:
        raise InstallError(f"{CONFIG_PATH}: {existing.problem}")
    try:
        text = text_of(existing)
    except ValueError as exc:
        raise InstallError(f"{CONFIG_PATH} {exc}; {UNTOUCHED}") from exc
    # Detection only seeds a new file. An existing one already says what the user wants.
    detected = detect_settings(target) if detect and text is None else {}
    decided = plan_config(text, detected=detected, overrides=overrides)
    if decided.text is None:
        return Action.note(CONFIG_PATH, decided.status), decided
    return Action.write(CONFIG_PATH, decided.status, decided.text.encode("utf-8")), decided


def _settings(target: Path, files: list[PayloadFile], missing: list[str]) -> tuple[list[Action], list[str]]:
    """Plan the hooks merge into `.claude/settings.json`; returns actions and warnings.

    `missing` lists kit files the hooks run that could not be installed. New
    hooks are not wired while it is non-empty: a hook whose script is absent
    fails with exit status 2, which Claude Code treats as "block".
    """
    fragment = load_fragment(files)
    if fragment is None:
        return [], []
    warnings: list[str] = []
    if fragment.ignored_keys:
        ignored = ", ".join(fragment.ignored_keys)
        warnings.append(f"the kit's settings fragment has keys that are not merged (only `hooks` is): {ignored}")
    existing = inspect(target, SETTINGS_PATH)
    if existing.problem:
        return [Action.error(SETTINGS_PATH, existing.problem)], warnings
    try:
        change = merge_hooks(text_of(existing), fragment.hooks)
    except (SettingsError, ValueError) as exc:
        return [Action.error(SETTINGS_PATH, f"{exc}; {UNTOUCHED}")], warnings
    if not change.changed:
        return ([Action.note(SETTINGS_PATH, UNCHANGED)] if existing.exists else []), warnings
    if missing:
        more = f" and {len(missing) - 1} more" if len(missing) > 1 else ""
        reason = f"hooks were not added because {missing[0]}{more} could not be installed; {UNTOUCHED}"
        return [Action.error(SETTINGS_PATH, reason)], warnings
    detail = "" if existing.exists else "new file"
    return [Action.write(SETTINGS_PATH, MERGED, change.text.encode("utf-8"), detail=detail)], warnings


def _block(target: Path, path: str, block: str, markers: Markers) -> Action:
    existing = inspect(target, path)
    if existing.problem:
        return Action.error(path, existing.problem)
    try:
        current = text_of(existing)
        updated = upsert_block(current, block, markers)
    except (BlockError, ValueError) as exc:
        return Action.error(path, f"{exc}; {UNTOUCHED}")
    if updated == current:
        return Action.note(path, UNCHANGED)
    return Action.write(path, CREATED if current is None else MERGED, updated.encode("utf-8"))


def _blocks(target: Path, blocks_root: Path, config: Any) -> list[Action]:
    """Plan the three managed blocks. A broken template is fatal; a damaged target file is not."""
    values = template_values(config)
    rendered = [(spec, load_block(blocks_root, spec, values)) for spec in BLOCKS]
    for spec, block in rendered:
        try:
            upsert_block(None, block, spec.markers)  # checks the template on its own
        except BlockError as exc:
            raise InstallError(f"block template {spec.template}: {exc}") from exc
    return [
        _block(target, spec.destination(lambda path: present(target, path)), block, spec.markers)
        for spec, block in rendered
    ]


def _manifest(target: Path, manifest: Manifest) -> Action:
    content = render_manifest(manifest).encode("utf-8")
    existing = inspect(target, MANIFEST_PATH)
    if existing.problem:
        return Action.error(MANIFEST_PATH, existing.problem)
    if existing.content is None:
        return Action.write(MANIFEST_PATH, CREATED, content)
    if same_content(existing.content, content):
        return Action.note(MANIFEST_PATH, UNCHANGED)
    return Action.write(MANIFEST_PATH, UPDATED, content)


def plan_install(
    target: Path,
    *,
    payload_root: Path,
    blocks_root: Path,
    overrides: Mapping[str, Any],
    force: bool,
    detect: bool,
) -> Plan:
    """Decide what an install into `target` would do. Reads only; raises `InstallError` when it cannot proceed."""
    files = scan_payload(payload_root)
    plan = Plan("install", kit_version(payload_root))
    previous, _, warnings = read_manifest(target)
    plan.warnings.extend(warnings)

    config_action, decided = _configuration(target, overrides, detect)
    plan.warnings.extend(decided.warnings)
    block_actions = _blocks(target, blocks_root, decided.config)

    records: dict[str, str] = {}
    for item in files:
        if item.ownership is Ownership.KIT:
            action, recorded = _kit_file(target, item, previous.files.get(item.path), force)
            if recorded:
                records[item.path] = recorded
            plan.actions.append(action)
        elif item.ownership is Ownership.USER:
            plan.actions.append(_user_file(target, item))
    # Files an earlier version installed and this one no longer ships.
    shipped = {item.path for item in files if item.ownership is Ownership.KIT}
    stale = {path: digest for path, digest in previous.files.items() if path not in shipped}
    stale_actions, kept = plan_removals(target, stale, force=force, detail=_STALE)
    records.update(kept)

    failed = [action.path for action in plan.actions if action.status == ERROR]
    settings_actions, warnings = _settings(target, files, [path for path in failed if path.startswith(HOOK_RUNTIME)])
    plan.warnings.extend(warnings)
    plan.actions += [*stale_actions, config_action, *settings_actions, *block_actions]
    if not decided.config.owners:
        plan.warnings.append(_NO_OWNERS)
    # Last on purpose: if a write fails midway, the old manifest still describes the old files.
    plan.actions.append(_manifest(target, Manifest(plan.kit_version, records)))
    return plan
