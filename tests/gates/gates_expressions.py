"""A small evaluator for the GitHub Actions expressions the gates workflows use.

It exists so the workflow tests can check what an `if:` or a concurrency
group evaluates to for a given event, instead of only looking for substrings.
It covers the subset those expressions need, with GitHub's documented rules:

* `&&` and `||` short-circuit and yield one of their operands;
* `false`, `0`, `''` and `null` are falsy, everything else is truthy;
* reading a property of something that is missing yields `null`;
* `==` and `startsWith` compare strings without regard to case;
* `format('{0}', value)` fills in placeholders.

Imported as `gates.gates_expressions` (see `gates_kit` for why the name is unique).
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Tuple

_TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:[^']|'')*')|(?P<number>\d+)|(?P<op>==|!=|&&|\|\||[!(),])|(?P<name>[A-Za-z_][\w.-]*))"
)


class ExpressionError(Exception):
    """The expression uses something this evaluator does not know."""


def unwrap(text: str) -> str:
    """The inside of a `${{ ... }}` wrapper (or the text itself when it has none)."""
    stripped = text.strip()
    if stripped.startswith("${{") and stripped.endswith("}}"):
        return stripped[3:-2]
    return stripped


def _tokens(text: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if not match:
            raise ExpressionError(f"cannot read expression at offset {position}")
        kind = str(match.lastgroup)
        found.append((kind, match.group(kind)))
        position = match.end()
    return found


def truthy(value: Any) -> bool:
    return not (value is None or value is False or value == 0 and not isinstance(value, str) or value == "")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    return left == right


class _Parser:
    def __init__(self, tokens: List[Tuple[str, str]], context: Mapping[str, Any]) -> None:
        self._tokens = tokens
        self._position = 0
        self._context = context

    def _peek(self) -> Tuple[str, str]:
        return self._tokens[self._position] if self._position < len(self._tokens) else ("end", "")

    def _take(self, value: str = "") -> Tuple[str, str]:
        token = self._peek()
        if value and token[1] != value:
            raise ExpressionError(f"expected {value!r} but found {token[1]!r}")
        self._position += 1
        return token

    def parse(self) -> Any:
        value = self._or()
        if self._peek()[0] != "end":
            raise ExpressionError(f"unexpected {self._peek()[1]!r}")
        return value

    # Operands are always parsed, so the grammar is checked even where GitHub
    # would short-circuit; reading a missing property is harmless (it is null).
    def _or(self) -> Any:
        value = self._and()
        while self._peek()[1] == "||":
            self._take()
            other = self._and()
            value = value if truthy(value) else other
        return value

    def _and(self) -> Any:
        value = self._equality()
        while self._peek()[1] == "&&":
            self._take()
            other = self._equality()
            value = other if truthy(value) else value
        return value

    def _equality(self) -> Any:
        value = self._unary()
        while self._peek()[1] in ("==", "!="):
            operator = self._take()[1]
            other = self._unary()
            value = _equal(value, other) if operator == "==" else not _equal(value, other)
        return value

    def _unary(self) -> Any:
        if self._peek()[1] == "!":
            self._take()
            return not truthy(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        kind, text = self._take()
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "number":
            return int(text)
        if text == "(":
            value = self._or()
            self._take(")")
            return value
        if kind != "name":
            raise ExpressionError(f"unexpected {text!r}")
        if self._peek()[1] == "(":
            return self._call(text)
        if text in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[text]
        return self._lookup(text)

    def _call(self, name: str) -> Any:
        self._take("(")
        arguments: List[Any] = []
        while self._peek()[1] != ")":
            arguments.append(self._or())
            if self._peek()[1] == ",":
                self._take()
        self._take(")")
        if name == "startsWith" and len(arguments) == 2:
            return _as_text(arguments[0]).lower().startswith(_as_text(arguments[1]).lower())
        if name == "format" and arguments:
            template = _as_text(arguments[0])
            return re.sub(r"\{(\d+)\}", lambda match: _as_text(arguments[int(match.group(1)) + 1]), template)
        raise ExpressionError(f"unknown function {name}")

    def _lookup(self, path: str) -> Any:
        value: Any = self._context
        for part in path.split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
        return value


def evaluate(expression: str, context: Mapping[str, Any]) -> Any:
    """Value of a GitHub Actions expression (with or without `${{ }}`) in `context`."""
    return _Parser(_tokens(unwrap(expression)), context).parse()
