"""Install and uninstall: validate the target, plan, then apply the plan.

This is the only module that changes the target. Everything it does is spelled
out beforehand in a `Plan`, so a dry run is the same code minus `apply_plan`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

from installer.errors import InstallError
from installer.fsops import delete_file, prune_empty_dirs, write_file
from installer.gitquery import work_tree_root
from installer.install_plan import plan_install
from installer.kitlib import DEFAULT_BLOCKS_ROOT, DEFAULT_PAYLOAD_ROOT
from installer.layout import PRUNE_ROOTS
from installer.plan import DELETE, WRITE, Plan
from installer.report import Entry, Report
from installer.uninstall_plan import plan_uninstall


def _same_directory(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve() or os.path.samefile(left, right)
    except OSError:
        return False


def validate_target(target: Path) -> Path:
    """Return the resolved target, or raise unless it is the top level of a git work tree.

    Installing into a subdirectory would put `.claude/` and `.github/` where
    neither Claude Code nor GitHub looks for them.
    """
    directory = Path(target)
    if not directory.is_dir():
        raise InstallError(f"target is not an existing directory: {target}")
    resolved = directory.resolve()
    top = work_tree_root(resolved)
    if not _same_directory(top, resolved):
        raise InstallError(f"{resolved} is not the top level of a git work tree (that is {top}); use --target {top}")
    return resolved


def apply_plan(target: Path, plan: Plan) -> None:
    """Carry out a plan: atomic writes, deletions, and pruning of the kit's emptied directories."""
    for action in plan.actions:
        if action.operation == WRITE:
            write_file(target, action.path, action.content or b"", executable=action.executable)
        elif action.operation == DELETE:
            delete_file(target, action.path)
            parent = action.path.rpartition("/")[0]
            if parent:
                prune_empty_dirs(target, parent, PRUNE_ROOTS)


def _report(plan: Plan, target: Path, dry_run: bool) -> Report:
    entries = [Entry(action.path, action.status, action.detail) for action in plan.actions]
    return Report(plan.mode, target, plan.kit_version, dry_run, entries, list(plan.warnings))


def install(
    target: Path,
    *,
    payload_root: Optional[Path] = None,
    blocks_root: Optional[Path] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    dry_run: bool = False,
    force: bool = False,
    detect: bool = True,
) -> Report:
    """Install or upgrade the kit in `target` and report what happened to every path.

    `overrides` is a nested dict in the shape of `.claude/reviewkit.json`; it
    wins over detected values and over an existing file. `dry_run` plans
    without writing. `force` overwrites kit-owned files that were modified
    locally. Raises `InstallError` when the run cannot start; problems with
    single paths are error entries in the report (`report.ok` is then False).
    """
    root = validate_target(target)
    plan = plan_install(
        root,
        payload_root=Path(payload_root or DEFAULT_PAYLOAD_ROOT),
        blocks_root=Path(blocks_root or DEFAULT_BLOCKS_ROOT),
        overrides=overrides or {},
        force=force,
        detect=detect,
    )
    if not dry_run:
        apply_plan(root, plan)
    return _report(plan, root, dry_run)


def uninstall(
    target: Path,
    *,
    payload_root: Optional[Path] = None,
    dry_run: bool = False,
    force: bool = False,
) -> Report:
    """Remove the kit from `target` and report what happened to every path.

    Kit files recorded in the manifest are removed (locally modified ones only
    with `force`), together with the managed blocks, the kit's hooks and the
    directories this leaves empty. User-owned files stay and are reported.
    """
    root = validate_target(target)
    plan = plan_uninstall(root, payload_root=Path(payload_root or DEFAULT_PAYLOAD_ROOT), force=force)
    if not dry_run:
        apply_plan(root, plan)
    return _report(plan, root, dry_run)
