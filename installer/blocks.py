"""Managed blocks: a marked region the installer owns inside a file the user owns.

Pure text functions. Everything outside the markers is the user's and comes
back byte for byte. Marker lines are matched as whole lines, so a sentence that
merely mentions a marker is ordinary text. Anything other than exactly one
begin line followed by one end line is refused: guessing where a damaged block
ends could delete the user's content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from installer.errors import InstallError

_BOM = "﻿"
_LINE = re.compile(r"[^\n]*\n|[^\n]+")


class BlockError(InstallError):
    """The markers in a file (or in a template) do not delimit exactly one block."""


@dataclass(frozen=True)
class Markers:
    """The two lines that delimit a managed block."""

    begin: str
    end: str


HTML_MARKERS = Markers("<!-- reviewkit:begin -->", "<!-- reviewkit:end -->")
HASH_MARKERS = Markers("# reviewkit:begin", "# reviewkit:end")


def _split_bom(text: str) -> tuple[str, str]:
    return (_BOM, text[1:]) if text.startswith(_BOM) else ("", text)


def _lines(text: str) -> list[str]:
    """Lines with their endings, split at `\\n` only so that joining them restores the text."""
    return _LINE.findall(text)


def _newline(text: str) -> str:
    """The file's line ending: CRLF only when every line uses it."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf and crlf == text.count("\n") else "\n"


def _without_trailing_blank(lines: list[str]) -> list[str]:
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    return lines[:end]


def _without_leading_blank(lines: list[str]) -> list[str]:
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    return lines[start:]


def _span(lines: list[str], markers: Markers) -> Optional[tuple[int, int]]:
    """Indexes of the begin and end lines, None when there is no block, or raise."""
    begins = [index for index, line in enumerate(lines) if line.strip() == markers.begin]
    ends = [index for index, line in enumerate(lines) if line.strip() == markers.end]
    if not begins and not ends:
        return None
    if len(begins) > 1 or len(ends) > 1:
        raise BlockError(f"more than one `{markers.begin}` / `{markers.end}` marker; expected a single block")
    if not ends:
        raise BlockError(f"begin marker `{markers.begin}` without an end marker `{markers.end}`")
    if not begins:
        raise BlockError(f"end marker `{markers.end}` without a begin marker `{markers.begin}`")
    if begins[0] > ends[0]:
        raise BlockError(f"end marker `{markers.end}` comes before the begin marker `{markers.begin}`")
    return begins[0], ends[0]


def _block_lines(block: str, markers: Markers) -> list[str]:
    """The block as lines without endings, checked to be exactly one marked block."""
    lines = block.replace("\r\n", "\n").split("\n")
    lines = _without_leading_blank(_without_trailing_blank(lines))
    if _span(lines, markers) != (0, len(lines) - 1):
        raise BlockError(f"a block must start with `{markers.begin}` and end with `{markers.end}`")
    return [markers.begin, *lines[1:-1], markers.end]


def upsert_block(existing: Optional[str], block: str, markers: Markers) -> str:
    """Return the file content with `block` in place.

    * no file (`None`): the block alone;
    * no markers: the block appended after exactly one blank line;
    * markers present: the lines from begin to end replaced, the rest untouched.

    The result equals `existing` when the block is already current.
    """
    wanted = _block_lines(block, markers)
    if existing is None:
        return "\n".join(wanted) + "\n"
    bom, body = _split_bom(existing)
    newline = _newline(body)
    rendered = newline.join(wanted) + newline
    lines = _lines(body)
    span = _span(lines, markers)
    if span is None:
        head = "".join(_without_trailing_blank(lines))
        if not head:
            return bom + rendered
        if not head.endswith("\n"):
            head += newline
        return bom + head + newline + rendered
    begin, end = span
    return bom + "".join(lines[:begin]) + rendered + "".join(lines[end + 1 :])


def remove_block(existing: str, markers: Markers) -> str:
    """Return the file content without the managed block.

    The blank lines that padded the block collapse to one between what came
    before and after. Text without a block is returned unchanged.
    """
    bom, body = _split_bom(existing)
    lines = _lines(body)
    span = _span(lines, markers)
    if span is None:
        return existing
    begin, end = span
    before = _without_trailing_blank(lines[:begin])
    after = _without_leading_blank(lines[end + 1 :])
    if before and after:
        return bom + "".join(before) + _newline(body) + "".join(after)
    return bom + "".join(before or after)
