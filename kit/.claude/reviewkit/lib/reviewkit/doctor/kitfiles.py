"""Compare kit-owned files with what the installer recorded in its manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Iterator, List

from .model import FAIL, INSTALL_FIX, OK, SKIP, WARN, Check

MANIFEST_PATH = ".claude/reviewkit/manifest.json"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def content_hash(data: bytes) -> str:
    """The installer's hash: sha256 with CRLF read as LF, binary content untouched."""
    return hashlib.sha256(data if b"\0" in data else data.replace(b"\r\n", b"\n")).hexdigest()


def _inside(root: Path, relative: str) -> bool:
    """Whether a manifest path stays inside the repository (the manifest is untrusted data)."""
    pure = PurePosixPath(relative)
    if not relative or "\\" in relative or pure.is_absolute() or ".." in pure.parts:
        return False
    try:
        (root / relative).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def kit_file_checks(root: Path) -> Iterator[Check]:
    path = root / MANIFEST_PATH
    if not path.is_file():
        yield Check("kit-files", SKIP, f"no {MANIFEST_PATH}: the kit was not placed here by its installer")
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        files = document["files"]
        if not isinstance(files, dict):
            raise ValueError("`files` must be an object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        yield Check("kit-files", WARN, f"{MANIFEST_PATH} cannot be read ({exc})", INSTALL_FIX)
        return
    missing: List[str] = []
    edited: List[str] = []
    counted = 0
    for relative, digest in sorted(files.items()):
        if not (isinstance(relative, str) and isinstance(digest, str) and _SHA256.match(digest)):
            continue
        if not _inside(root, relative):
            continue
        counted += 1
        target = root / relative
        if not target.is_file():
            missing.append(relative)
        elif content_hash(target.read_bytes()) != digest:
            edited.append(relative)
    if missing:
        yield Check("kit-files", FAIL, f"{len(missing)} kit file(s) missing: {', '.join(missing[:5])}", INSTALL_FIX)
    if edited:
        summary = f"{len(edited)} kit file(s) edited locally: {', '.join(edited[:5])} (upgrades leave them alone)"
        fix = (
            "Keep your own rules in REVIEW.md, CLAUDE.md and .claude/reviewkit.json; "
            "restore kit files with the installer's --force."
        )
        yield Check("kit-files", WARN, summary, fix)
    if not missing and not edited:
        version = document.get("kit_version") if isinstance(document.get("kit_version"), str) else "unknown"
        yield Check("kit-files", OK, f"{counted} kit file(s) match what kit version {version} installed")
