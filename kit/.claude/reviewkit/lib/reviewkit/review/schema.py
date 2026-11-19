"""The structured output the review session must return.

One source of truth: the workflow passes this schema to Claude Code with
`--json-schema`, the review skill ships a copy as a reference file (a test
keeps them identical), and `findings.normalize` re-validates every field,
because a schema-shaped answer is still untrusted content.
"""

from __future__ import annotations

import json

SEVERITIES = ("important", "nit", "pre_existing")
PRIOR_STATUSES = ("fixed", "still_open", "withdrawn")

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_STRINGS = {"type": "array", "items": _STRING}

_FINDING = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "line", "severity", "title", "body", "evidence", "quote", "lens"],
    "properties": {
        "path": _STRING,
        "line": _INTEGER,
        "start_line": _INTEGER,
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "title": _STRING,
        "body": _STRING,
        "evidence": _STRING,
        "quote": _STRING,
        "lens": _STRING,
        "checklist_id": _STRING,
        "cause_path": _STRING,
        "cause_line": _INTEGER,
        "in_scope": {"type": "boolean"},
    },
}

_REJECTED = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "line", "title", "reason"],
    "properties": {"path": _STRING, "line": _INTEGER, "title": _STRING, "reason": _STRING, "lens": _STRING},
}

_PRIOR = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "status"],
    "properties": {
        "id": _STRING,
        "status": {"type": "string", "enum": list(PRIOR_STATUSES)},
        "note": _STRING,
    },
}

_COVERAGE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lenses_planned", "lenses_completed", "candidates", "confirmed"],
    "properties": {
        "lenses_planned": _STRINGS,
        "lenses_completed": _STRINGS,
        "candidates": _INTEGER,
        "confirmed": _INTEGER,
        "checklist_items": _STRINGS,
        "notes": _STRING,
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "rejected", "prior", "coverage"],
    "properties": {
        "summary": _STRING,
        "findings": {"type": "array", "items": _FINDING},
        "rejected": {"type": "array", "items": _REJECTED},
        "prior": {"type": "array", "items": _PRIOR},
        "coverage": _COVERAGE,
    },
}


def schema_json() -> str:
    """Compact single-line JSON, safe inside single quotes on a command line."""
    text = json.dumps(REVIEW_SCHEMA, separators=(",", ":"), sort_keys=True)
    if "'" in text or "$" in text or "\n" in text:
        raise ValueError("review schema must stay free of quotes, dollars and newlines")
    return text
