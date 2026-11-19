"""The manifest: what the installer wrote, so an upgrade can tell kit content from local edits.

Stored in the target as `{"files": {"<path>": "<sha256>"}, "kit_version": "<version>"}`
with sorted keys and no timestamps, so installing the same kit twice writes the
same bytes.

Hashes and comparisons treat CRLF and LF as the same line ending. Git may check
text files out with either, depending on the machine, and a line-ending
conversion is not a local modification. For LF content the recorded value is
the plain sha256 of the file.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from installer.fsops import UnsafePathError, relative_parts

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Manifest:
    """The kit version last installed and the hash of every kit-owned file it wrote."""

    kit_version: str = "0"
    files: Mapping[str, str] = field(default_factory=dict)


def _normalized(data: bytes) -> bytes:
    """Text with LF line endings. Binary content (anything holding a NUL byte) is left alone."""
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def content_hash(data: bytes) -> str:
    """sha256 of the content, independent of the checkout's line endings."""
    return hashlib.sha256(_normalized(data)).hexdigest()


def same_content(left: bytes, right: bytes) -> bool:
    """Whether two versions of a file differ at most in line endings."""
    return left == right or _normalized(left) == _normalized(right)


def _usable(path: Any, digest: Any) -> bool:
    if not isinstance(path, str) or not isinstance(digest, str) or not _SHA256.match(digest):
        return False
    try:
        relative_parts(path)
    except UnsafePathError:
        return False
    return True


def parse_manifest(text: str) -> tuple[Manifest, tuple[str, ...]]:
    """Parse a manifest into `(Manifest, ignored_paths)`; raise `ValueError` when unusable.

    The file comes from the target repository, so it is treated as untrusted:
    an entry whose path could leave the target, or whose hash is malformed, is
    dropped and returned in `ignored_paths` instead of being acted on.
    """
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise ValueError("expected a JSON object")
    files = document.get("files", {})
    if not isinstance(files, dict):
        raise ValueError("`files` must be an object")
    version = document.get("kit_version")
    kept = {path: digest for path, digest in files.items() if _usable(path, digest)}
    ignored = tuple(path for path in files if path not in kept)
    return Manifest(version if isinstance(version, str) else "0", kept), ignored


def render_manifest(manifest: Manifest) -> str:
    """Serialize deterministically: sorted keys, two-space indent, trailing newline."""
    document = {"kit_version": manifest.kit_version, "files": dict(manifest.files)}
    return json.dumps(document, indent=2, sort_keys=True) + "\n"
