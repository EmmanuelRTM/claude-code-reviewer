"""Decisions for kit-owned files: write, leave alone, or remove.

Pure functions: they take the payload content, the destination's current
content and the hash the manifest recorded, and say what to do. A file whose
content no longer matches what the installer wrote has been modified locally
and is left alone unless `force` is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from installer.manifest import content_hash, same_content
from installer.report import CREATED, REMOVED, SKIPPED, UNCHANGED, UPDATED


@dataclass(frozen=True)
class Decision:
    """What to do with one kit-owned path, and what the manifest should say afterwards."""

    status: str
    write: bool = False
    delete: bool = False
    recorded: Optional[str] = None  # hash to keep in the manifest; None drops the entry


def decide_kit_file(payload: bytes, current: Optional[bytes], recorded: Optional[str], *, force: bool) -> Decision:
    """Decide how to bring one destination in line with the payload.

    `current` is None when the destination does not exist; `recorded` is None
    when the manifest has no entry for it. A skipped file keeps its old record,
    so it is still recognised as modified on the next run.
    """
    wanted = content_hash(payload)
    if current is None:
        return Decision(CREATED, write=True, recorded=wanted)
    if same_content(current, payload):
        return Decision(UNCHANGED, recorded=wanted)
    if force or content_hash(current) == recorded:
        return Decision(UPDATED, write=True, recorded=wanted)
    return Decision(SKIPPED, recorded=recorded)


def decide_removal(current: Optional[bytes], recorded: str, *, force: bool) -> Optional[Decision]:
    """Decide whether to remove a file the manifest lists.

    Used for files a newer kit no longer ships and for every kit file on
    uninstall. Returns None when the file is already gone. A modified file is
    kept and stays in the manifest, so a later run with `force` can remove it.
    """
    if current is None:
        return None
    if force or content_hash(current) == recorded:
        return Decision(REMOVED, delete=True)
    return Decision(SKIPPED, recorded=recorded)
