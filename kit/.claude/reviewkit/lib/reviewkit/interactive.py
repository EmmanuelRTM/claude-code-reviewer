"""Arguments for the `@claude` workflow on issues and pull requests.

Local hooks do not run in CI, so the session is told the contract directly and
is allowed to run the project's own check commands without asking.
"""

from __future__ import annotations

import re
import shlex

from .config import Config

# A command is pre-approved only when it is plain words: nothing a shell would chain, expand or redirect.
_PLAIN_COMMAND = re.compile(r"[A-Za-z0-9_./:=@%+, -]+\Z")


def _contract(cfg: Config) -> str:
    checks = [command for command in (cfg.commands.lint, cfg.commands.build, cfg.commands.test) if command]
    listed = "; ".join(checks) if checks and all(_PLAIN_COMMAND.match(c) for c in checks) else "see CLAUDE.md"
    return (
        "This repository follows the verification contract in CLAUDE.md. Local hooks do not run here, so before you "
        f"finish, run the project checks yourself ({listed}) and fix what fails without skipping or weakening tests. "
        "End every commit message with a Verified-By trailer naming what you ran. If a check still fails after three "
        "fix rounds, stop and ask a person. Do not change REVIEW.md, docs/reliability-checklist.md, .claude or "
        ".github/workflows unless the request is explicitly about them."
    )


def interactive_args(cfg: Config) -> str:
    values = ["--max-turns", str(cfg.interactive.max_turns)]
    if cfg.interactive.model:
        values += ["--model", cfg.interactive.model]
    commands = [c for c in (cfg.commands.test, cfg.commands.lint, cfg.commands.build) if c and _PLAIN_COMMAND.match(c)]
    if commands:
        values += ["--allowedTools", ",".join(f"Bash({command})" for command in commands)]
    values += ["--append-system-prompt", _contract(cfg)]
    if any("'" in value for value in values):
        raise ValueError("an argument contains a single quote and cannot be passed safely")
    return " ".join(shlex.quote(value) for value in values)
