"""`rk.py` command line: one entry point, commands registered per module.

Each command module exposes `register(subparsers)` and gives every parser a
`handler(args) -> int`. Adding a command never touches this file's logic.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Optional, Sequence

from . import __version__

COMMAND_MODULES = (
    "reviewkit.cmd_local",  # fingerprint, verify marker, checks, gate log
    "reviewkit.cmd_review",  # CI review: prepare, publish, local context
    "reviewkit.cmd_interactive",  # arguments for the @claude workflow
    "reviewkit.cmd_gates",  # risk tiers, approvals, issue intake, metrics, checklist
    "reviewkit.cmd_doctor",  # installation health
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rk", description="Verification-first review kit helpers.")
    parser.add_argument("--version", action="version", version=f"reviewkit {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for name in COMMAND_MODULES:
        importlib.import_module(name).register(subparsers)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return int(handler(args) or 0)
