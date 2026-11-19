"""One end-to-end check against the real `kit/` payload and `blocks/` templates."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from installer_kit import snapshot

from installer.kitlib import DEFAULT_BLOCKS_ROOT, DEFAULT_PAYLOAD_ROOT
from installer.operations import install, uninstall
from installer.payload import Ownership, scan_payload
from installer.report import KEPT, UNCHANGED

RK = DEFAULT_PAYLOAD_ROOT / ".claude" / "reviewkit" / "rk.py"


@pytest.mark.skipif(not RK.exists(), reason="the real payload (kit/.claude/reviewkit/rk.py) does not exist yet")
def test_installing_the_real_kit_twice_is_a_no_op(repo, tmp_path: Path) -> None:
    # Frozen copies: the kit is still being written, and an edit between the two
    # runs would look like an upgrade. Symlinks are kept so the scan can refuse them.
    payload = tmp_path / "kit-snapshot"
    blocks = tmp_path / "blocks-snapshot"
    shutil.copytree(DEFAULT_PAYLOAD_ROOT, payload, symlinks=True)
    shutil.copytree(DEFAULT_BLOCKS_ROOT, blocks, symlinks=True)
    kit_owned = [item.path for item in scan_payload(payload) if item.ownership is Ownership.KIT]

    first = install(repo.path, payload_root=payload, blocks_root=blocks)
    assert first.ok, [(entry.path, entry.detail) for entry in first.errors]
    assert (repo.path / ".claude/reviewkit/rk.py").is_file()
    manifest = json.loads((repo.path / ".claude/reviewkit/manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest["files"]) == kit_owned
    assert not [path for path in snapshot(repo.path) if "__pycache__" in path or path.endswith(".pyc")]

    before = snapshot(repo.path)
    second = install(repo.path, payload_root=payload, blocks_root=blocks)
    assert second.ok
    assert second.changed is False
    assert {entry.status for entry in second.entries} <= {UNCHANGED, KEPT}
    assert snapshot(repo.path) == before

    removed = uninstall(repo.path, payload_root=payload)
    assert removed.ok, [(entry.path, entry.detail) for entry in removed.errors]
    assert not (repo.path / ".claude/reviewkit").exists()
    assert not [path for path in kit_owned if (repo.path / path).exists()]
