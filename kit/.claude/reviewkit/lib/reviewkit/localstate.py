"""Local, gitignored state under `.claude/state/`.

Holds the verification marker, per-session counters and the gate log. Nothing
here is shared or trusted across machines; it only records what happened in
this checkout.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, MutableMapping

from .paths import state_dir


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def remember(table: MutableMapping[str, Any], key: str, value: Any, limit: int = 50) -> None:
    """Set `table[key]`, most recent last, keeping at most `limit` entries."""
    table.pop(key, None)
    table[key] = value
    while len(table) > limit:
        table.pop(next(iter(table)))


class LocalState:
    def __init__(self, project_dir: Path) -> None:
        self.dir = state_dir(Path(project_dir))

    def _path(self, name: str) -> Path:
        if not name or name != os.path.basename(name) or name in (".", ".."):
            raise ValueError(f"invalid state file name: {name!r}")
        return self.dir / name

    def read_json(self, name: str, default: Any) -> Any:
        try:
            value = json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        if default is not None and not isinstance(value, type(default)):
            return default
        return value

    def write_json(self, name: str, data: Any) -> None:
        target = self._path(name)
        self.dir.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(self.dir))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temp_name, str(target))
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def append_line(self, name: str, record: dict) -> None:
        target = self._path(name)
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def read_lines(self, name: str) -> list:
        try:
            lines = self._path(name).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records = []
        for line in lines:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records
