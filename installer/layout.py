"""Fixed locations in a target repository that the installer manages.

All paths are target-relative POSIX paths, the form used everywhere in the
installer and in the manifest.
"""

from __future__ import annotations

from installer.kitlib import CONFIG_RELATIVE_PATH

# Generated from the library's configuration dataclasses.
CONFIG_PATH = CONFIG_RELATIVE_PATH
# The payload's copy is a hooks fragment that is merged into the target's file.
SETTINGS_PATH = ".claude/settings.json"
# Records what the installer wrote, so upgrades can tell kit content from local edits.
MANIFEST_PATH = ".claude/reviewkit/manifest.json"

CLAUDE_MD_PATH = "CLAUDE.md"
GITIGNORE_PATH = ".gitignore"
# GitHub reads only the first CODEOWNERS file it finds, in this order.
CODEOWNERS_CANDIDATES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

# Skills the kit owns outright. The `verify` skill is the user's once created.
KIT_SKILLS = ("pr-review", "bug-finding", "finding-verification")

# What the kit's hooks execute: the hook scripts and the library behind them.
HOOK_RUNTIME = (".claude/hooks/", ".claude/reviewkit/")

# Directories that belong to the kit: removed when a removal leaves them empty.
PRUNE_ROOTS = (
    ".claude/reviewkit",
    ".claude/hooks",
    *(f".claude/skills/{name}" for name in KIT_SKILLS),
)
