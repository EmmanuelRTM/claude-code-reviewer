"""What the kit ships, and who owns each path once it is installed.

The payload directory mirrors target-repository paths: `kit/.claude/hooks/x.py`
installs to `<target>/.claude/hooks/x.py`. Ownership decides what an install,
an upgrade and an uninstall may do with a path.
"""

from __future__ import annotations

import enum
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from installer.errors import InstallError
from installer.fsops import relative_parts
from installer.layout import (
    CLAUDE_MD_PATH,
    CODEOWNERS_CANDIDATES,
    CONFIG_PATH,
    GITIGNORE_PATH,
    MANIFEST_PATH,
    SETTINGS_PATH,
)

_SKIPPED_DIRECTORIES = frozenset({"__pycache__"})
_SKIPPED_NAMES = frozenset({".DS_Store"})
_SKIPPED_SUFFIXES = (".pyc", ".pyo")

_USER_FILES = frozenset(
    {
        "REVIEW.md",
        "docs/reliability-checklist.md",
        ".github/pull_request_template.md",
        ".github/dependabot.yml",
    }
)
_USER_DIRECTORIES = (".claude/skills/verify/",)

# Paths the installer produces itself: the generated configuration and manifest,
# and the files it only ever edits through a managed block.
_GENERATED = frozenset({CONFIG_PATH, MANIFEST_PATH, CLAUDE_MD_PATH, GITIGNORE_PATH, *CODEOWNERS_CANDIDATES})

_LIBRARY_INIT = (".claude", "reviewkit", "lib", "reviewkit", "__init__.py")
_VERSION = re.compile(r"""^__version__\s*=\s*["']([^"'\n]+)["']""", re.MULTILINE)


class Ownership(enum.Enum):
    """What the installer may do with a payload path."""

    KIT = "kit-owned"  # replaced on upgrade, removed on uninstall
    USER = "create-if-absent"  # written once, then the user's: never overwritten or removed
    SETTINGS = "settings-fragment"  # merged into the target's settings, never copied
    GENERATED = "generated"  # produced by the installer; a payload copy is ignored


@dataclass(frozen=True)
class PayloadFile:
    """One installable file: its target-relative path and where its content comes from."""

    path: str
    source: Path
    ownership: Ownership
    executable: bool


def classify(path: str) -> Ownership:
    """Ownership class of a target-relative POSIX path.

    Kit-owned covers the library (`.claude/reviewkit/**`), the hook scripts,
    the kit's skills and subagents, and its workflows and issue templates; any
    other payload path is kit-owned as well. The exceptions are listed here.
    """
    if path == SETTINGS_PATH:
        return Ownership.SETTINGS
    if path in _GENERATED:
        return Ownership.GENERATED
    if path in _USER_FILES or path.startswith(_USER_DIRECTORIES):
        return Ownership.USER
    return Ownership.KIT


def scan_payload(payload_root: Path) -> list[PayloadFile]:
    """Every installable file under `payload_root`, sorted by path.

    Bytecode caches are skipped. Symbolic links and anything inside git
    metadata are refused: a payload is plain files, and following a link could
    copy content from outside the kit.
    """
    root = Path(payload_root)
    if not root.is_dir():
        raise InstallError(f"payload directory not found: {root}")
    found = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIPPED_DIRECTORIES)
        for name in dirnames:
            if os.path.islink(os.path.join(directory, name)):
                relative = (Path(directory) / name).relative_to(root).as_posix()
                raise InstallError(f"payload contains a symbolic link: {relative}")
        for name in filenames:
            if name in _SKIPPED_NAMES or name.endswith(_SKIPPED_SUFFIXES):
                continue
            source = Path(directory) / name
            relative = source.relative_to(root).as_posix()
            info = os.lstat(source)
            if stat.S_ISLNK(info.st_mode):
                raise InstallError(f"payload contains a symbolic link: {relative}")
            if not stat.S_ISREG(info.st_mode):
                raise InstallError(f"payload entry is not a regular file: {relative}")
            relative_parts(relative)  # raises for a path the installer must never write
            found.append(PayloadFile(relative, source, classify(relative), bool(info.st_mode & 0o111)))
    return sorted(found, key=lambda item: item.path)


def kit_version(payload_root: Path) -> str:
    """The library version shipped in the payload, or "0" when it has no library.

    Read as text: the payload is data to copy, not code for the installer to import.
    """
    try:
        text = Path(payload_root).joinpath(*_LIBRARY_INIT).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "0"
    match = _VERSION.search(text)
    return match.group(1) if match else "0"
