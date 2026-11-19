"""Generate and update `.claude/reviewkit.json`.

Defaults, types and validation all come from the library's configuration
dataclasses. A new file is built in layers: defaults, then detected values,
then explicit overrides (`--config`, then `--set`). An existing file belongs to
the user: only explicit overrides are applied to it, and it is rewritten only
when they change something.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from installer.errors import InstallError
from installer.jsonio import dump_json
from installer.kitlib import Config, ConfigError, config_to_dict, parse_config
from installer.layout import CONFIG_PATH
from installer.report import CREATED, UNCHANGED, UPDATED

_BOOLEANS = {"true": True, "false": False}
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")


@dataclass(frozen=True)
class ConfigPlan:
    """The validated configuration and what to do with the file (`text` is None: leave it)."""

    config: Any
    status: str
    text: Optional[str]
    warnings: tuple[str, ...] = ()


def _field_names(node: Any) -> list[str]:
    return [spec.name for spec in dataclasses.fields(node)]


def _default_for(key: str) -> Any:
    """The default value at a dotted key, which also tells the type a value must have."""
    node: Any = Config()
    walked: list[str] = []
    for part in key.split("."):
        if not dataclasses.is_dataclass(node):
            raise InstallError(f"--set {key}: unknown setting ({'.'.join(walked)} is a single value)")
        if part not in _field_names(node):
            known = ", ".join(".".join([*walked, name]) for name in _field_names(node))
            raise InstallError(f"--set {key}: unknown setting (known here: {known})")
        node = getattr(node, part)
        walked.append(part)
    return node


def coerce_value(key: str, raw: str) -> Any:
    """Convert the text of `--set KEY=VALUE` to the type of the setting's default."""
    default = _default_for(key)
    if dataclasses.is_dataclass(default):
        keys = ", ".join(f"{key}.{name}" for name in _field_names(default))
        raise InstallError(f"--set {key}: this is a section, set one of its keys ({keys})")
    if isinstance(default, bool):  # before int: a bool is an int in Python
        value = _BOOLEANS.get(raw.strip().lower())
        if value is None:
            raise InstallError(f"--set {key}: expected true or false, got {raw!r}")
        return value
    if isinstance(default, int):
        if not _INTEGER.match(raw.strip()):
            raise InstallError(f"--set {key}: expected an integer, got {raw!r}")
        return int(raw.strip())
    if isinstance(default, tuple):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def parse_assignments(assignments: Sequence[str]) -> dict[str, Any]:
    """Turn `--set KEY=VALUE` arguments into a nested overrides dict; later ones win."""
    overrides: dict[str, Any] = {}
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        key = key.strip()
        if not separator or not key:
            raise InstallError(f"--set {assignment!r}: expected KEY=VALUE")
        value = coerce_value(key, raw)
        *sections, leaf = key.split(".")
        node = overrides
        for section in sections:
            node = node.setdefault(section, {})
        node[leaf] = value
    return overrides


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of `base` with `overlay` on top: objects merge, everything else is replaced."""
    merged = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _validated(data: dict[str, Any], source: str) -> tuple[Any, tuple[str, ...]]:
    try:
        config, warnings = parse_config(data)
    except ConfigError as exc:
        raise InstallError(f"invalid configuration ({source}): {exc}") from exc
    return config, tuple(f"{CONFIG_PATH}: {warning}" for warning in warnings)


def _load_existing(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InstallError(f"{CONFIG_PATH} is not valid JSON ({exc}); it was left untouched") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{CONFIG_PATH} must hold a JSON object; it was left untouched")
    return data


def plan_config(existing: Optional[str], *, detected: Mapping[str, Any], overrides: Mapping[str, Any]) -> ConfigPlan:
    """Decide the configuration and whether its file needs writing.

    `existing` is the current file's text, or None when there is none.
    Raises `InstallError` naming the offending key when the result is invalid,
    and for an existing file that cannot be parsed (it is never overwritten).
    """
    if existing is None:
        data = deep_merge(deep_merge(config_to_dict(Config()), detected), overrides)
        config, warnings = _validated(data, "defaults with detected values and overrides")
        return ConfigPlan(config, CREATED, dump_json(data), warnings)
    current = _load_existing(existing)
    data = deep_merge(current, overrides)
    source = f"{CONFIG_PATH} with overrides applied" if overrides else f"{CONFIG_PATH}, left untouched"
    config, warnings = _validated(data, source)
    if json.dumps(data, sort_keys=True) == json.dumps(current, sort_keys=True):
        return ConfigPlan(config, UNCHANGED, None, warnings)
    return ConfigPlan(config, UPDATED, dump_json(data), warnings)
