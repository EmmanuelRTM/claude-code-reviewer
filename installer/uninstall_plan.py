"""Plan an uninstall: remove what the installer added, and nothing that is the user's.

Kit files are identified by the manifest, hooks by the kit's settings fragment,
blocks by their markers. Files that became the user's when they were created
(`REVIEW.md`, the configuration, the `verify` skill, ...) are reported as kept.
"""

from __future__ import annotations

from pathlib import Path

from installer.blocks import BlockError, remove_block
from installer.fsops import inspect
from installer.layout import CONFIG_PATH, MANIFEST_PATH, SETTINGS_PATH
from installer.manifest import Manifest, render_manifest, same_content
from installer.payload import Ownership, PayloadFile, kit_version, scan_payload
from installer.plan import Action, Plan
from installer.planning import UNTOUCHED, load_fragment, plan_removals, present, read_manifest, text_of
from installer.report import KEPT, UNCHANGED, UPDATED
from installer.settings_merge import SettingsError, remove_hooks
from installer.templates import BLOCKS

# The scripts are removed either way, and a hook whose script is gone blocks Claude Code.
_HOOKS_BY_HAND = "if it still wires the kit's hooks, remove them by hand"
_NO_MANIFEST = (
    f"no manifest at {MANIFEST_PATH}: nothing is recorded as installed, so no kit files were removed. "
    "Managed blocks and hooks were still looked for."
)


def _settings(target: Path, files: list[PayloadFile]) -> list[Action]:
    """Plan the removal of the kit's hook groups from `.claude/settings.json`."""
    fragment = load_fragment(files)
    if fragment is None:
        return []
    existing = inspect(target, SETTINGS_PATH)
    if existing.problem:
        return [Action.error(SETTINGS_PATH, f"{existing.problem}; {_HOOKS_BY_HAND}")]
    if not existing.exists:
        return []
    try:
        change = remove_hooks(text_of(existing) or "", fragment.hooks)
    except (SettingsError, ValueError) as exc:
        return [Action.error(SETTINGS_PATH, f"{exc}; {UNTOUCHED}; {_HOOKS_BY_HAND}")]
    if not change.changed:
        return [Action.note(SETTINGS_PATH, UNCHANGED)]
    if change.delete:
        return [Action.delete(SETTINGS_PATH, "it held only the kit's hooks")]
    return [Action.write(SETTINGS_PATH, UPDATED, change.text.encode("utf-8"), detail="kit hooks removed")]


def _blocks(target: Path) -> list[Action]:
    """Plan the removal of every managed block. The files themselves always stay."""
    actions: list[Action] = []
    for spec in BLOCKS:
        # Every place the block may have gone to, in case the file was moved since.
        for path in spec.candidates:
            if not present(target, path):
                continue
            existing = inspect(target, path)
            if existing.problem:
                actions.append(Action.error(path, existing.problem))
                continue
            try:
                current = text_of(existing) or ""
                stripped = remove_block(current, spec.markers)
            except (BlockError, ValueError) as exc:
                actions.append(Action.error(path, f"{exc}; {UNTOUCHED}"))
                continue
            if stripped == current:
                actions.append(Action.note(path, UNCHANGED))
            else:
                actions.append(Action.write(path, UPDATED, stripped.encode("utf-8"), detail="managed block removed"))
    return actions


def _user_files(target: Path, files: list[PayloadFile]) -> list[Action]:
    """Report the user-owned files that stay behind."""
    paths = [item.path for item in files if item.ownership is Ownership.USER] + [CONFIG_PATH]
    return [Action.note(path, KEPT) for path in sorted(paths) if present(target, path)]


def _manifest(target: Path, remaining: Manifest) -> list[Action]:
    """Delete the manifest, or shrink it to the files that were left in place.

    Keeping those records is what lets a later run with `force` remove them.
    """
    existing = inspect(target, MANIFEST_PATH)
    if existing.problem:
        return [Action.error(MANIFEST_PATH, existing.problem)]
    if existing.content is None:
        return []
    if not remaining.files:
        return [Action.delete(MANIFEST_PATH)]
    content = render_manifest(remaining).encode("utf-8")
    if same_content(existing.content, content):
        return [Action.note(MANIFEST_PATH, UNCHANGED)]
    count = len(remaining.files)
    detail = f"still lists {count} file{'' if count == 1 else 's'} left in place"
    return [Action.write(MANIFEST_PATH, UPDATED, content, detail=detail)]


def plan_uninstall(target: Path, *, payload_root: Path, force: bool) -> Plan:
    """Decide what an uninstall from `target` would do. Reads only."""
    files = scan_payload(payload_root)
    plan = Plan("uninstall", kit_version(payload_root))
    manifest, found, warnings = read_manifest(target)
    plan.warnings.extend(warnings)
    if not found:
        plan.warnings.append(_NO_MANIFEST)

    removals, remaining = plan_removals(target, manifest.files, force=force)
    plan.actions += [*removals, *_settings(target, files), *_blocks(target), *_user_files(target, files)]
    # Last, so an interrupted run can be repeated: the manifest still names what is left.
    plan.actions += _manifest(target, Manifest(manifest.kit_version, remaining))
    return plan
