"""JSON output shared by the files the installer writes for people to read and edit."""

from __future__ import annotations

import json
from typing import Any


def dump_json(data: Any) -> str:
    """Two-space indented JSON with a trailing newline, keys in their given order.

    Non-ASCII text is written as is, so a diff stays readable. Text that UTF-8
    cannot encode (a lone surrogate from an escaped sequence) falls back to
    escapes rather than making the write fail.
    """
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(data, indent=2, ensure_ascii=True)
    return text + "\n"
