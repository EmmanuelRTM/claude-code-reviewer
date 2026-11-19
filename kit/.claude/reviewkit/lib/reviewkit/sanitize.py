"""Make model-written text safe to post on GitHub.

Review text is produced by a model that has read untrusted pull request
content, so it is treated as untrusted too: secrets are redacted, hidden
markers and HTML comments are neutralized, and `@mentions` outside code are
defanged so a review can never ping people or teams.
"""

from __future__ import annotations

import re

from . import markers

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRETS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd)\s*[:=]\s*[\"']?)([A-Za-z0-9_\-/+=.]{16,})"
)
_CODE_SPAN = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
_MENTION = re.compile(r"@(?=[A-Za-z0-9])")
REDACTED = "[redacted]"


def redact_secrets(text: str) -> str:
    for pattern in _SECRETS:
        text = pattern.sub(REDACTED, text)
    return _ASSIGNED_SECRET.sub(lambda m: m.group(1) + REDACTED, text)


def defang_mentions(text: str) -> str:
    """Break `@name` outside code spans with a zero-width space so nobody is notified."""
    parts = _CODE_SPAN.split(text)
    return "".join(part if index % 2 else _MENTION.sub("@​", part) for index, part in enumerate(parts))


def clean_text(value: object, limit: int) -> str:
    """Sanitize one text field and bound its length."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n").strip()
    text = defang_mentions(markers.neutralize(redact_secrets(text)))
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def one_line(value: object, limit: int) -> str:
    return " ".join(clean_text(value, limit * 2).split())[:limit]
