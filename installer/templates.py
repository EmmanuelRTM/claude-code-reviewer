"""Block templates: which files carry a managed block, and what fills the placeholders.

Templates live in `blocks/` and already contain their marker lines. A
`{{NAME}}` placeholder is replaced by a value computed from the configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from installer.blocks import HASH_MARKERS, HTML_MARKERS, Markers
from installer.errors import InstallError
from installer.layout import CLAUDE_MD_PATH, CODEOWNERS_CANDIDATES, CONFIG_PATH, GITIGNORE_PATH

_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
# What GitHub accepts as a code owner: @user, @org/team or an email address.
_OWNER = re.compile(r"(?:@[^\s#@]+|[^\s#@]+@[^\s#@]+)\Z")

# Paths whose changes need a human code owner, in the order they are written.
PROTECTED_PATHS = ("/REVIEW.md", "/docs/reliability-checklist.md", "/CLAUDE.md", "/.claude/", "/.github/")
_PLACEHOLDER_OWNER = "@your-team"


class TemplateError(InstallError):
    """A template could not be read or names a placeholder nobody fills."""


@dataclass(frozen=True)
class BlockSpec:
    """One managed block: its template, its markers and where it may live."""

    template: str
    markers: Markers
    candidates: tuple[str, ...]  # target paths in lookup order; the first is created when none exists

    def destination(self, exists: Callable[[str], bool]) -> str:
        """The file that carries the block: the first candidate that exists, else the first.

        Matters for CODEOWNERS only. GitHub reads the first file it finds among
        the candidates, so adding `.github/CODEOWNERS` next to an existing root
        `CODEOWNERS` would silently switch off every rule in the existing file.
        """
        return next((path for path in self.candidates if exists(path)), self.candidates[0])


BLOCKS = (
    BlockSpec("CLAUDE.md.block", HTML_MARKERS, (CLAUDE_MD_PATH,)),
    BlockSpec("CODEOWNERS.block", HASH_MARKERS, CODEOWNERS_CANDIDATES),
    BlockSpec("gitignore.block", HASH_MARKERS, (GITIGNORE_PATH,)),
)


def render_template(template: str, values: Mapping[str, str]) -> str:
    """Replace every `{{NAME}}`; a placeholder without a value is an error.

    Values are inserted verbatim and not scanned again, so a command that
    happens to contain braces cannot be mistaken for a placeholder.
    """
    unknown = sorted({name for name in _PLACEHOLDER.findall(template) if name not in values})
    if unknown:
        raise TemplateError("unknown placeholder " + ", ".join("{{" + name + "}}" for name in unknown))
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def _code_span(text: str) -> str:
    """Markdown inline code that stays intact when the text itself contains backticks."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def command_text(command: str, key: str) -> str:
    """How a configured command reads in CLAUDE.md, or how to configure a missing one."""
    command = command.strip()
    if not command:
        return f"not configured (set {key} in {CONFIG_PATH})"
    if "\n" in command or "\r" in command:
        return f"see {key} in {CONFIG_PATH}"  # a list item cannot hold several lines
    return _code_span(command)


def codeowners_rules(owners: Sequence[str]) -> str:
    """One CODEOWNERS line per protected path, columns aligned.

    Without owners the same lines are written commented out, as a reminder:
    an active rule without an owner would mean "nobody owns this".
    """
    for owner in owners:
        if not _OWNER.match(owner):
            raise InstallError(
                f"owners: {owner!r} is not a code owner GitHub accepts (use @user, @org/team or an email address)"
            )
    width = max(len(path) for path in PROTECTED_PATHS)
    prefix, names = ("", " ".join(owners)) if owners else ("# ", _PLACEHOLDER_OWNER)
    return "\n".join(f"{prefix}{path.ljust(width)}  {names}" for path in PROTECTED_PATHS)


def template_values(config: Any) -> dict[str, str]:
    """Placeholder values for every block, computed from a `reviewkit` Config."""
    commands = config.commands
    return {
        "TEST_COMMAND": command_text(commands.test, "commands.test"),
        "LINT_COMMAND": command_text(commands.lint, "commands.lint"),
        "BUILD_COMMAND": command_text(commands.build, "commands.build"),
        "CODEOWNERS_RULES": codeowners_rules(config.owners),
    }


def load_block(blocks_root: Path, spec: BlockSpec, values: Mapping[str, str]) -> str:
    """Read one template from `blocks_root` and render it."""
    path = Path(blocks_root) / spec.template
    try:
        template = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TemplateError(f"cannot read block template {path}: {exc}") from exc
    try:
        return render_template(template, values)
    except TemplateError as exc:
        raise TemplateError(f"{spec.template}: {exc}") from exc
