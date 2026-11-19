"""Assemble the review instructions from the trusted base commit.

A change must never rewrite the rules that judge it, so `REVIEW.md` and
everything it includes are read from git objects of the base commit, never from
the pull request's files. The include rules follow the managed Code Review
service (root files and project skills by `@name` line, subdirectory
`REVIEW.md` files for the paths a change touches), so the same files keep
working if a repository later moves to that service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from ..gitio import GitError, git

MAX_INCLUDES = 8
MAX_ROOT_BYTES = 150_000
MAX_SUBDIR_BYTES = 150_000
MAX_SKILL_BYTES = 30_000

_INCLUDE = re.compile(r"@(\S+)\Z")
_SKILL_PATH = re.compile(r"\.claude/skills/[^/\s]+/SKILL\.md\Z")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n?", re.S)

# A subdirectory REVIEW.md in or below a directory with one of these names is not applied.
EXCLUDED_DIRECTORIES = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "testdata",
        "fixtures",
        "__fixtures__",
        "golden",
        "corpus",
        "snapshots",
        "__snapshots__",
        "example",
        "examples",
        "sample",
        "samples",
        "demo",
        "demos",
        "generated",
        "vendor",
        "third_party",
        "third-party",
        "thirdparty",
        "node_modules",
        "bower_components",
        "external",
        "extern",
        "deps",
        "godeps",
        "pods",
        "carthage",
        "site-packages",
        "contrib",
    }
)


@dataclass
class Instructions:
    text: str = ""
    sources: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _tree_paths(repo: Path, commit: str) -> Set[str]:
    out = git(["-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", "-z", commit], repo)
    return {path for path in out.split("\0") if path}


def _show(repo: Path, commit: str, path: str) -> Optional[str]:
    try:
        return git(["show", f"{commit}:{path}"], repo, raw=True).decode("utf-8", "replace")
    except GitError:
        return None


def _expand_root(repo: Path, commit: str, root_path: str, text: str, tree: Set[str], result: Instructions) -> str:
    lines: List[str] = []
    in_fence = False
    includes = 0
    used = len(text.encode("utf-8"))
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _INCLUDE.match(line.strip())
        name = match.group(1) if match else ""
        is_skill = bool(name) and _SKILL_PATH.match(name) is not None
        is_root_file = bool(name) and "/" not in name
        if not name or name not in tree or name == root_path or not (is_skill or is_root_file):
            lines.append(line)
            continue
        if includes >= MAX_INCLUDES:
            result.skipped.append((name, f"over the limit of {MAX_INCLUDES} included files"))
            continue
        content = _show(repo, commit, name) or ""
        if is_skill:
            content = _FRONTMATTER.sub("", content.replace("\r\n", "\n"), count=1)
        size = len(content.encode("utf-8"))
        if is_skill and size > MAX_SKILL_BYTES:
            result.skipped.append((name, f"skill file over the limit of {MAX_SKILL_BYTES} bytes"))
            continue
        if used + size > MAX_ROOT_BYTES:
            result.skipped.append((name, f"would pass the limit of {MAX_ROOT_BYTES} bytes"))
            continue
        includes += 1
        used += size
        result.sources.append(name)
        lines.append(content.rstrip("\n"))
    return "\n".join(lines)


def _subdirectory_files(tree: Set[str], changed_paths: Sequence[str], file_name: str) -> List[str]:
    """`<dir>/<file_name>` for every parent directory of a changed path, skipping excluded names."""
    found: List[str] = []
    for changed in changed_paths:
        parts = changed.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directory = parts[:depth]
            candidate = "/".join([*directory, file_name])
            excluded = any(part.lower() in EXCLUDED_DIRECTORIES for part in directory)
            if candidate in tree and not excluded and candidate not in found:
                found.append(candidate)
    return sorted(found)


def assemble_instructions(
    repo: Path, base_commit: str, changed_paths: Sequence[str], root_path: str = "REVIEW.md"
) -> Instructions:
    result = Instructions()
    tree = _tree_paths(repo, base_commit)
    sections: List[str] = []
    root = _show(repo, base_commit, root_path) if root_path in tree else None
    if root is not None:
        result.sources.append(root_path)
        body = _expand_root(repo, base_commit, root_path, root.replace("\r\n", "\n"), tree, result)
        sections.append(body.rstrip("\n"))
    used = 0
    for path in _subdirectory_files(tree, changed_paths, Path(root_path).name):
        if path == root_path:
            continue
        content = (_show(repo, base_commit, path) or "").replace("\r\n", "\n")
        size = len(content.encode("utf-8"))
        if used + size > MAX_SUBDIR_BYTES:
            result.skipped.append((path, f"would pass the limit of {MAX_SUBDIR_BYTES} bytes"))
            continue
        used += size
        result.sources.append(path)
        directory = path.rsplit("/", 1)[0]
        sections.append(f"## Additional checks for changes under `{directory}/`\n\n{content.rstrip()}")
    result.text = "\n\n".join(section for section in sections if section.strip())
    if result.text:
        result.text += "\n"
    return result


def applicable_claude_md(repo: Path, base_commit: str, changed_paths: Sequence[str]) -> List[str]:
    """Nested `CLAUDE.md` files (base versions) in parent directories of the changed files."""
    return _subdirectory_files(_tree_paths(repo, base_commit), changed_paths, "CLAUDE.md")
