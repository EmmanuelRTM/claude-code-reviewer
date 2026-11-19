"""Suggest check commands and the default branch by reading the target repository.

Detection only reads files; nothing from the repository is executed. Every
suggested command is one of a fixed set of strings. The repository decides
which of them apply, never what they contain, so a hostile file cannot smuggle
a command into the generated configuration.

Ecosystems are tried in a fixed order (Node, Python, Go, Rust, Make). The
first one to offer a command for a slot wins; later ones only fill gaps.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from installer.gitquery import origin_default_branch

_MAX_BYTES = 1_000_000  # no real manifest is larger; do not load arbitrary files whole
_MAX_DIRECTORIES = 2_000  # bound for the search below `tests/`

_PACKAGE_MANAGERS = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"))
_NPM_PLACEHOLDER = "no test specified"  # the `test` script that `npm init` writes
_PYTHON_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg")
_RUFF_FILES = ("ruff.toml", ".ruff.toml")
_RUFF_SECTION = re.compile(r"^[ \t]*\[[ \t]*tool\.ruff[ \t]*[.\]]", re.MULTILINE)
_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")  # the order in which make looks for them
# A rule line `name:` or `name::`, but not an assignment such as `name := value`.
_MAKE_TARGET = re.compile(r"^(test|lint|build)[ \t]*:(?!:?=)", re.MULTILINE)


def _read(path: Path) -> str:
    """Text of a regular file, or "" when it is missing, unreadable or implausibly large."""
    try:
        if not path.is_file():
            return ""
        with open(path, "rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
    except OSError:
        return ""
    return "" if len(data) > _MAX_BYTES else data.decode("utf-8", errors="replace")


def _node(root: Path) -> dict[str, str]:
    try:
        package = json.loads(_read(root / "package.json") or "null")
    except ValueError:
        return {}
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict):
        return {}
    manager = next((name for lockfile, name in _PACKAGE_MANAGERS if (root / lockfile).is_file()), "npm")

    def defined(script: str) -> bool:
        value = scripts.get(script)
        return isinstance(value, str) and bool(value.strip())

    commands = {}
    if defined("test") and _NPM_PLACEHOLDER not in scripts["test"]:
        commands["test"] = f"{manager} run test"
    for slot in ("lint", "build"):
        if defined(slot):
            commands[slot] = f"{manager} run {slot}"
    start = next((script for script in ("dev", "start") if defined(script)), None)
    if start:
        commands["start"] = f"{manager} run {start}"
    return commands


def _has_test_modules(directory: Path) -> bool:
    """Whether a `test_*.py` file exists anywhere below `directory` (links are not followed)."""
    if not directory.is_dir():
        return False
    for visited, (_, dirnames, filenames) in enumerate(os.walk(directory)):
        if any(name.startswith("test_") and name.endswith(".py") for name in filenames):
            return True
        if visited >= _MAX_DIRECTORIES:
            return False
        dirnames[:] = [name for name in dirnames if name != "__pycache__" and not name.startswith(".")]
    return False


def _python(root: Path) -> dict[str, str]:
    commands = {}
    if any((root / name).is_file() for name in _PYTHON_MARKERS) or _has_test_modules(root / "tests"):
        commands["test"] = "python3 -m pytest -q"
    configured = _RUFF_SECTION.search(_read(root / "pyproject.toml")) is not None
    if configured or any((root / name).is_file() for name in _RUFF_FILES):
        commands["lint"] = "ruff check ."
    return commands


def _go(root: Path) -> dict[str, str]:
    if not (root / "go.mod").is_file():
        return {}
    return {"test": "go test ./...", "lint": "go vet ./...", "build": "go build ./..."}


def _rust(root: Path) -> dict[str, str]:
    if not (root / "Cargo.toml").is_file():
        return {}
    return {"test": "cargo test", "lint": "cargo clippy -- -D warnings", "build": "cargo build"}


def _make(root: Path) -> dict[str, str]:
    for name in _MAKEFILES:
        if (root / name).is_file():
            targets = set(_MAKE_TARGET.findall(_read(root / name)))
            return {slot: f"make {slot}" for slot in ("test", "lint", "build") if slot in targets}
    return {}


_ECOSYSTEMS = (_node, _python, _go, _rust, _make)


def detect_commands(root: Path) -> dict[str, str]:
    """Suggested `test`, `lint`, `build` and `start` commands; absent keys were not detected."""
    commands: dict[str, str] = {}
    for ecosystem in _ECOSYSTEMS:
        for slot, command in ecosystem(Path(root)).items():
            commands.setdefault(slot, command)
    return commands


def detect_settings(root: Path) -> dict[str, Any]:
    """Detected values in the shape of the configuration file, ready to merge onto the defaults."""
    settings: dict[str, Any] = {}
    commands = detect_commands(root)
    if commands:
        settings["commands"] = commands
    branch = origin_default_branch(root)
    if branch:
        settings["default_branch"] = branch
    return settings
