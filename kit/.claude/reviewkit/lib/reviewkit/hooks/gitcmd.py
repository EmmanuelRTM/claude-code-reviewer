"""Recognise `git commit` in a Bash command and read its `Verified-By` trailer.

This is a small shell-aware scanner, not a regex over the raw string: quoted
text, comments and here-document bodies are data, so `echo "git commit"` or a
commit message that mentions the trailer of another command never counts.
Like every hook-side match it is best effort (an alias such as `git ci` is not
seen); the pull request review is the backstop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_PLACEHOLDER = re.compile("\x00H(\\d+)\x00")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_TRAILER = re.compile(r"(?im)(?:^|\s)Verified-By:[ \t]*(.*?)[ \t]*$")
_OPT_OUT = re.compile(r"none\b\s*\((.*)\)\s*\Z", re.I | re.S)

_SEPARATORS = ";&|()`\n"
_WRAPPERS = {"command", "exec", "nohup", "time", "env", "{", "!", "then", "do", "else"}
_GLOBAL_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
_MIN_REASON_CHARS = 3
_MAX_MESSAGE_FILE_BYTES = 1_000_000


@dataclass
class Word:
    text: str
    quoted: bool = False


@dataclass(frozen=True)
class TrailerInfo:
    message_visible: bool = False
    present: bool = False
    value: str = ""
    opted_out: bool = False
    reason: str = ""


@dataclass
class _Scan:
    statements: List[List[Word]] = field(default_factory=list)
    heredocs: List[str] = field(default_factory=list)


def _extract_heredocs(command: str, bodies: List[str]) -> str:
    """Replace each here-document (operator and body) with a placeholder word."""
    out, position = [], 0
    while True:
        match = _HEREDOC.search(command, position)
        if not match:
            out.append(command[position:])
            return "".join(out)
        line_end = command.find("\n", match.end())
        if line_end == -1:
            out.append(command[position:])
            return "".join(out)
        strip_tabs = match.group(0).startswith("<<-")
        delimiter, cursor, body_lines, closed = match.group(2), line_end + 1, [], False
        while cursor <= len(command):
            next_end = command.find("\n", cursor)
            line = command[cursor:] if next_end == -1 else command[cursor:next_end]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                closed = True
                cursor = len(command) if next_end == -1 else next_end + 1
                break
            body_lines.append(line)
            if next_end == -1:
                break
            cursor = next_end + 1
        if not closed:  # not a real here-document: leave the text alone
            out.append(command[position : match.end()])
            position = match.end()
            continue
        bodies.append("\n".join(body_lines))
        out.append(command[position : match.start()])
        out.append(f"\x00H{len(bodies) - 1}\x00")
        out.append(command[match.end() : line_end + 1])
        position = cursor


def _matching_paren(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def _tokenize(text: str, scan: _Scan) -> None:
    """Split `text` into statements of words; nested `$(...)` is scanned recursively."""
    words: List[Word] = []
    current: Optional[Word] = None

    def end_word() -> None:
        nonlocal current
        if current is not None:
            words.append(current)
            current = None

    def end_statement() -> None:
        nonlocal words
        end_word()
        if words:
            scan.statements.append(words)
        words = []

    def add(chars: str, quoted: bool = False) -> None:
        nonlocal current
        if current is None:
            current = Word("")
        current.text += chars
        current.quoted = current.quoted or quoted

    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char in " \t":
            end_word()
        elif char == "#" and current is None:
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
            continue
        elif char == "\\" and i + 1 < n:
            if text[i + 1] != "\n":
                add(text[i + 1])
            i += 1
        elif char == "'":
            close = text.find("'", i + 1)
            close = n if close == -1 else close
            add(text[i + 1 : close], quoted=True)
            i = close
        elif char == '"':
            i = _double_quoted(text, i + 1, add, scan)
        elif char == "$" and text.startswith("$(", i):
            end_statement()
            i += 1
        elif char in _SEPARATORS:
            end_statement()
        else:
            add(char)
        i += 1
    end_statement()


def _double_quoted(text: str, start: int, add, scan: _Scan) -> int:
    """Consume a double-quoted string starting after the opening quote; return the closing index."""
    add("", quoted=True)
    i, n = start, len(text)
    while i < n and text[i] != '"':
        if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$`':
            add(text[i + 1], quoted=True)
            i += 2
            continue
        if text.startswith("$(", i):
            close = _matching_paren(text, i + 1)
            _tokenize(text[i + 2 : close], scan)  # a substitution is a command of its own
            add(text[i : close + 1], quoted=True)
            i = close + 1
            continue
        add(text[i], quoted=True)
        i += 1
    return i


def _scan(command: str) -> _Scan:
    scan = _Scan()
    _tokenize(_extract_heredocs(command or "", scan.heredocs), scan)
    return scan


def _commit_arguments(words: List[Word]) -> Optional[List[Word]]:
    """Words after `commit` when the statement runs `git ... commit`, else None."""
    index = 0
    while index < len(words):
        word = words[index]
        if (
            not word.quoted
            and (_ASSIGNMENT.match(word.text) or word.text in _WRAPPERS)
            or not word.quoted
            and word.text.startswith("-")
            and index > 0
            and words[index - 1].text == "env"
        ):
            index += 1
        else:
            break
    if index >= len(words) or words[index].text.rsplit("/", 1)[-1] != "git":
        return None
    index += 1
    while index < len(words):
        text = words[index].text
        if text in _GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
        elif text.startswith("-"):
            index += 1
        else:
            return words[index + 1 :] if text == "commit" else None
    return None


def _find_commit(scan: _Scan) -> Optional[List[Word]]:
    for statement in scan.statements:
        arguments = _commit_arguments(statement)
        if arguments is not None:
            return arguments
    return None


def is_git_commit(command: str) -> bool:
    return _find_commit(_scan(command)) is not None


def _expand(text: str, heredocs: List[str]) -> str:
    return _PLACEHOLDER.sub(lambda m: heredocs[int(m.group(1))], text)


def _message_sources(arguments: List[Word], heredocs: List[str], cwd: Path) -> List[str]:
    messages: List[str] = []
    index = 0
    while index < len(arguments):
        text = arguments[index].text
        following = arguments[index + 1].text if index + 1 < len(arguments) else None
        inline_message = text.startswith("--message=")
        inline_file = text.startswith("--file=")
        is_message = text == "--message" or (re.fullmatch(r"-[A-Za-z]*m", text) is not None)
        is_file = text in ("-F", "--file")
        if inline_message:
            messages.append(_expand(text.split("=", 1)[1], heredocs))
        elif is_message and following is not None:
            messages.append(_expand(following, heredocs))
            index += 1
        elif inline_file or (is_file and following is not None):
            name = text.split("=", 1)[1] if inline_file else following
            content = _read_message_file(cwd, name or "")
            if content is not None:
                messages.append(content)
            index += 0 if inline_file else 1
        index += 1
    return messages


def _read_message_file(cwd: Path, name: str) -> Optional[str]:
    path = Path(name) if Path(name).is_absolute() else Path(cwd) / name
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MESSAGE_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_trailer(command: str, cwd: Path) -> TrailerInfo:
    """Describe the `Verified-By` trailer of the commit `command` would create."""
    scan = _scan(command)
    arguments = _find_commit(scan)
    if arguments is None:
        return TrailerInfo()
    messages = _message_sources(arguments, scan.heredocs, cwd)
    if not messages:
        return TrailerInfo(message_visible=False)
    matches = _TRAILER.findall("\n".join(messages))
    if not matches:
        return TrailerInfo(message_visible=True)
    value = matches[-1].strip()
    opt_out = _OPT_OUT.match(value)
    reason = opt_out.group(1).strip() if opt_out else ""
    opted_out = len(reason) >= _MIN_REASON_CHARS
    return TrailerInfo(True, True, value, opted_out, reason if opted_out else "")
