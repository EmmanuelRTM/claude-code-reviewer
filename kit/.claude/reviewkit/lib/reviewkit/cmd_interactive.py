"""CLI for the `@claude` workflow: `interactive args`."""

from __future__ import annotations

import sys
from pathlib import Path

from . import ci
from .config import ConfigError, load_config
from .gitio import repo_root
from .interactive import interactive_args


def _args(_args) -> int:
    project = repo_root(Path(".").resolve()) or Path(".").resolve()
    try:
        value = interactive_args(load_config(project).config)
    except (ConfigError, ValueError) as exc:
        print(f"reviewkit: {exc}", file=sys.stderr)
        return 1
    ci.set_output("claude_args", value)
    print(value)
    return 0


def register(subparsers) -> None:
    interactive = subparsers.add_parser("interactive", help="the @claude workflow for issues and pull requests")
    actions = interactive.add_subparsers(dest="interactive_command", metavar="<action>")
    actions.add_parser("args", help="print the Claude arguments derived from the project config").set_defaults(
        handler=_args
    )
