"""Hidden HTML-comment markers that carry machine state inside GitHub comments.

A marker is one line: `<!-- reviewkit:<kind> <json> -->`. The JSON is escaped
so a payload can never close the comment or smuggle in another marker.
Markers are only ever trusted when the comment was written by the workflow's
own bot account; callers check authorship before decoding.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

_PREFIX = "reviewkit"
_ANY_MARKER = re.compile(r"<!--\s*reviewkit:[a-z_]+(?:\s.*?)?\s*-->", re.DOTALL)


def _pattern(kind: str) -> re.Pattern[str]:
    return re.compile(r"<!--\s*reviewkit:" + re.escape(kind) + r"(?:\s+(.*?))?\s*-->", re.DOTALL)


def encode(kind: str, payload: Optional[Mapping[str, Any]] = None) -> str:
    if payload is None:
        return f"<!-- {_PREFIX}:{kind} -->"
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("-", "\\u002d")
    return f"<!-- {_PREFIX}:{kind} {text} -->"


def decode_all(kind: str, text: str) -> list:
    found = []
    for match in _pattern(kind).finditer(text or ""):
        raw = match.group(1)
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def decode_last(kind: str, text: str) -> Optional[dict]:
    found = decode_all(kind, text)
    return found[-1] if found else None


def has(kind: str, text: str) -> bool:
    return _pattern(kind).search(text or "") is not None


def strip(text: str) -> str:
    """Remove every reviewkit marker from `text`."""
    return _ANY_MARKER.sub("", text or "")


def neutralize(text: str) -> str:
    """Make untrusted text safe to embed next to our markers (no HTML comments)."""
    return (text or "").replace("<!--", "&lt;!--")
