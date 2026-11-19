"""Checks that need only the working tree: configuration, hooks, primitives, rules.

Each checker looks at one layer and yields `Check`s. A checker that crashes is
reported as a failure of that one check; the rest still run, so `doctor` always
gives a complete picture.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Mapping, Optional, Tuple

from ..checklist import lint_checklist, parse_checklist
from ..checks import configured_checks
from ..config import CONFIG_RELATIVE_PATH, Config, ConfigError, effective_gate_mode, hooks_disabled, load_config
from ..gitio import GitError, git, repo_root
from ..review.instructions import MAX_ROOT_BYTES
from .kitfiles import kit_file_checks
from .model import FAIL, INSTALL_FIX, OK, SKIP, WARN, Check
from .ownership import uncovered

HOOK_SCRIPTS = (
    ("the commit gate (PreToolUse)", "PreToolUse", "verify_commit_gate.py"),
    ("the checks on Stop", "Stop", "run_checks_on_stop.py"),
)
LIBRARY_FILES = (".claude/reviewkit/rk.py", ".claude/reviewkit/lib/reviewkit/__init__.py")
SKILLS = ("pr-review", "bug-finding", "finding-verification", "verify")
AGENTS = ("bug-finder", "finding-verifier")
REVIEW_WORKFLOW = "claude-review.yml"
OPTIONAL_WORKFLOWS = {
    "claude.yml": "@claude in issues and pull requests",
    "risk-approval.yml": "risk-tiered human approval",
    "issue-intake.yml": "acceptance criteria on issues",
    "review-metrics.yml": "monthly metrics issue",
}
TOKEN_SECRET = "CLAUDE_CODE_OAUTH_TOKEN"
UNWANTED_CREDENTIAL = "ANTHROPIC_API_KEY"  # this kit never uses an API key
CODEOWNERS_CANDIDATES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
BLOCK_BEGIN = "<!-- reviewkit:begin -->"
BLOCK_END = "<!-- reviewkit:end -->"

_NAME = re.compile(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", re.M)
_PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")


@dataclass(frozen=True)
class Context:
    root: Path
    cfg: Config
    env: Mapping[str, str]
    in_git: bool


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    except OSError:
        return None


def _shown(items: Iterable[str], limit: int = 5) -> str:
    items = list(items)
    more = f" and {len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def _config(root: Path) -> Tuple[Config, Check]:
    if not (root / CONFIG_RELATIVE_PATH).is_file():
        return Config(), Check("config", FAIL, f"{CONFIG_RELATIVE_PATH} is missing", INSTALL_FIX)
    try:
        loaded = load_config(root)
    except ConfigError as exc:
        fix = f"Correct {CONFIG_RELATIVE_PATH}; defaults are used until then."
        return Config(), Check("config", FAIL, str(exc), fix)
    if loaded.warnings:
        fix = f"Remove or correct these keys in {CONFIG_RELATIVE_PATH}."
        return loaded.config, Check("config", WARN, "; ".join(loaded.warnings), fix)
    return loaded.config, Check("config", OK, f"{CONFIG_RELATIVE_PATH} is valid")


def _commands(ctx: Context) -> Iterator[Check]:
    configured = configured_checks(ctx.cfg)
    unset = [name for name in ctx.cfg.checks.on_stop if not getattr(ctx.cfg.commands, name, "")]
    fix = f'Set "commands" in {CONFIG_RELATIVE_PATH}, for example {{"test": "npm test"}}.'
    if not configured:
        names = ", ".join(f"commands.{name}" for name in unset) or "checks.on_stop"
        summary = f"no check runs when Claude stops ({names} is empty): nothing proves the work"
        yield Check("commands", WARN, summary, fix)
    elif unset:
        yield Check("commands", WARN, f"checks.on_stop names {_shown(unset)} but no command is set for it", fix)
    else:
        yield Check("commands", OK, "on stop: " + ", ".join(f"{name} (`{command}`)" for name, command in configured))


def _hook_commands(settings: Mapping, event: str) -> Iterator[str]:
    groups = (settings.get("hooks") or {}).get(event) if isinstance(settings.get("hooks"), dict) else None
    for group in groups if isinstance(groups, list) else []:
        for hook in (group.get("hooks") if isinstance(group, dict) else None) or []:
            if isinstance(hook, dict):
                yield " ".join(str(part) for part in [hook.get("command", ""), *(hook.get("args") or [])])


def _hook_problems(ctx: Context) -> Iterator[Check]:
    settings_path = ctx.root / ".claude" / "settings.json"
    text = _read(settings_path)
    if text is None:
        yield Check("hooks", FAIL, ".claude/settings.json is missing: no hook is wired", INSTALL_FIX)
        return
    try:
        settings = json.loads(text)
        if not isinstance(settings, dict):
            raise ValueError("expected a JSON object")
    except ValueError as exc:
        summary = f".claude/settings.json cannot be read ({exc}): no hook runs"
        yield Check("hooks", FAIL, summary, "Fix the JSON syntax.")
        return
    unwired = [
        label
        for label, event, script in HOOK_SCRIPTS
        if not any(script in command for command in _hook_commands(settings, event))
    ]
    if unwired:
        yield Check("hooks", FAIL, f".claude/settings.json does not wire {' or '.join(unwired)}", INSTALL_FIX)
    for relative in [f".claude/hooks/{script}" for _, _, script in HOOK_SCRIPTS] + list(LIBRARY_FILES):
        source = _read(ctx.root / relative)
        if source is None:
            yield Check("hooks", FAIL, f"{relative} is missing", INSTALL_FIX)
            continue
        try:
            compile(source, relative, "exec")
        except SyntaxError as exc:
            yield Check("hooks", FAIL, f"{relative} does not compile (line {exc.lineno}: {exc.msg})", INSTALL_FIX)
    for name in ("settings.json", "settings.local.json"):
        try:
            disabled = json.loads(_read(ctx.root / ".claude" / name) or "{}").get("disableAllHooks") is True
        except (ValueError, AttributeError):
            disabled = False
        if disabled:
            fix = f"Remove disableAllHooks from .claude/{name} to turn the hooks back on."
            yield Check("hooks", WARN, f"disableAllHooks is set in .claude/{name}: neither hook runs", fix)
    if hooks_disabled(ctx.env):
        switches = [name for name in ("REVIEWKIT_DISABLE", "GITHUB_ACTIONS", "CI") if ctx.env.get(name)]
        fix = "Unset the variable to run the hooks here. In CI they stay off on purpose."
        yield Check("hooks", WARN, f"hooks are switched off in this environment by {_shown(switches)}", fix)


def _hooks(ctx: Context) -> List[Check]:
    mode = effective_gate_mode(ctx.cfg, ctx.env)
    ok = Check("hooks", OK, f"commit gate (mode: {mode}) and Stop checks are wired, and their scripts compile")
    return list(_hook_problems(ctx)) or [ok]


def _ignore(ctx: Context) -> Iterator[Check]:
    if not ctx.in_git:
        yield Check("ignore", SKIP, "not a git repository")
        return
    try:
        git(["check-ignore", "-q", ".claude/state/probe"], ctx.root)
    except GitError:
        fix = "Add `.claude/state/` to .gitignore."
        yield Check("ignore", WARN, ".claude/state/ is not ignored: local markers and logs could be committed", fix)
    else:
        yield Check("ignore", OK, ".claude/state/ is ignored by git")


def _named(ctx: Context, check_id: str, kind: str, names: Iterable[str], pattern: str) -> Iterator[Check]:
    names = list(names)
    problems = []
    for name in names:
        text = _read(ctx.root / pattern.format(name=name))
        match = _NAME.search(text.split("\n---", 1)[0]) if text and text.startswith("---") else None
        if text is None:
            problems.append(f"{name} is missing")
        elif match is None or match.group(1).strip() != name:
            problems.append(f"{name} does not declare `name: {name}` in its frontmatter")
    if problems:
        yield Check(check_id, FAIL, f"{kind}: " + "; ".join(problems), INSTALL_FIX)
    else:
        yield Check(check_id, OK, f"{kind}: " + ", ".join(names))


def _skills(ctx: Context) -> Iterator[Check]:
    return _named(ctx, "skills", "skills", SKILLS, ".claude/skills/{name}/SKILL.md")


def _agents(ctx: Context) -> Iterator[Check]:
    return _named(ctx, "agents", "subagents", AGENTS, ".claude/agents/{name}.md")


def _workflow_problems(ctx: Context) -> Iterator[Check]:
    directory = ctx.root / ".github" / "workflows"
    review = _read(directory / REVIEW_WORKFLOW)
    if review is None:
        summary = f".github/workflows/{REVIEW_WORKFLOW} is missing: pull requests are not reviewed"
        yield Check("workflows", FAIL, summary, INSTALL_FIX)
    elif TOKEN_SECRET not in review:
        fix = "Restore the workflow with the installer (--force overwrites local edits)."
        yield Check("workflows", FAIL, f"{REVIEW_WORKFLOW} does not use the {TOKEN_SECRET} secret", fix)
    absent = [f"{name} ({purpose})" for name, purpose in OPTIONAL_WORKFLOWS.items() if not (directory / name).is_file()]
    if absent:
        fix = "Re-run the installer to add them, or ignore this if you left the layer out on purpose."
        yield Check("workflows", WARN, "not installed: " + "; ".join(absent), fix)
    kit_workflows = (REVIEW_WORKFLOW, *OPTIONAL_WORKFLOWS)
    keyed = [name for name in kit_workflows if UNWANTED_CREDENTIAL in (_read(directory / name) or "")]
    if keyed:
        fix = (
            f"Pass only `claude_code_oauth_token: ${{{{ secrets.{TOKEN_SECRET} }}}}`. "
            "When an API key is set, Claude Code uses it before the subscription token."
        )
        summary = f"{_shown(keyed)} mentions {UNWANTED_CREDENTIAL}: this kit runs on the subscription token"
        yield Check("workflows", WARN, summary, fix)


def _workflows(ctx: Context) -> List[Check]:
    ok = Check("workflows", OK, f"review, @claude, approval, intake and metrics workflows use {TOKEN_SECRET} only")
    return list(_workflow_problems(ctx)) or [ok]


def _rules(ctx: Context) -> Iterator[Check]:
    relative = ctx.cfg.review.instructions_path
    path = ctx.root / relative
    if not path.is_file():
        fix = f"Add {relative} with what Important means here (the installer creates a starter)."
        yield Check("rules", WARN, f"{relative} is missing: the reviewer has no repository rules to apply", fix)
        return
    size = path.stat().st_size
    if size > MAX_ROOT_BYTES:
        summary = f"{relative} is {size:,} bytes; anything past {MAX_ROOT_BYTES:,} bytes is not given to the reviewer"
        yield Check("rules", WARN, summary, "Shorten it, or move detail into files it includes with @path.")
    else:
        yield Check("rules", OK, f"{relative} ({size:,} bytes)")


def _checklist(ctx: Context) -> Iterator[Check]:
    relative = ctx.cfg.review.checklist_path
    text = _read(ctx.root / relative)
    lint = "Run `python3 -I .claude/reviewkit/rk.py checklist lint` for every line."
    if text is None:
        fix = "Add the file (the installer creates a starter) or point review.checklist_path at it."
        yield Check("checklist", WARN, f"{relative} is missing: the reviewer applies no reliability checklist", fix)
        return
    issues = lint_checklist(text)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        summary = f"{relative}: " + "; ".join(f"line {issue.line}: {issue.message}" for issue in errors[:3])
        yield Check("checklist", FAIL, summary, lint)
    elif issues:
        first = issues[0]
        summary = f"{relative}: {len(issues)} item(s) need attention, first on line {first.line}: {first.message}"
        yield Check("checklist", WARN, summary, lint)
    else:
        yield Check("checklist", OK, f"{relative}: {len(parse_checklist(text))} item(s)")


def _codeowners(ctx: Context) -> Iterator[Check]:
    found = next((name for name in CODEOWNERS_CANDIDATES if (ctx.root / name).is_file()), None)
    fix = (
        "Re-run the installer with --set owners=@you-or-your-team, "
        "then require code owner review on the default branch."
    )
    if found is None:
        summary = "no CODEOWNERS file: nobody is required to look at changes to the review rules, hooks and workflows"
        yield Check("codeowners", WARN, summary, fix)
        return
    samples = {
        ".claude/": ".claude/settings.json",
        ".github/workflows/": f".github/workflows/{REVIEW_WORKFLOW}",
        ctx.cfg.review.instructions_path: ctx.cfg.review.instructions_path,
        ctx.cfg.review.checklist_path: ctx.cfg.review.checklist_path,
    }
    open_paths = uncovered(_read(ctx.root / found) or "", samples)
    if open_paths:
        yield Check("codeowners", WARN, f"{found} names no owner for {_shown(open_paths)}", fix)
    else:
        yield Check("codeowners", OK, f"{found} names owners for the review rules, hooks and workflows")


def _claude_md(ctx: Context) -> Iterator[Check]:
    text = _read(ctx.root / "CLAUDE.md")
    if text is None or BLOCK_BEGIN not in text:
        what = "CLAUDE.md is missing" if text is None else "CLAUDE.md has no verification contract block"
        yield Check("claude-md", WARN, f"{what}: Claude is not told what done means here", INSTALL_FIX)
        return
    block = text.split(BLOCK_BEGIN, 1)[1].split(BLOCK_END, 1)[0]
    unfilled = sorted(set(_PLACEHOLDER.findall(block)))
    if unfilled:
        fix = f"Set the commands in {CONFIG_RELATIVE_PATH} and re-run the installer."
        yield Check("claude-md", WARN, f"CLAUDE.md still has placeholders: {_shown(unfilled)}", fix)
    else:
        yield Check("claude-md", OK, "CLAUDE.md carries the verification contract")


def _committed(ctx: Context) -> Iterator[Check]:
    if not ctx.in_git:
        yield Check("committed", SKIP, "not a git repository")
        return
    review = ctx.cfg.review
    paths = [".claude", ".github", review.instructions_path, review.checklist_path, ":(exclude).claude/state"]
    lines = [line for line in git(["status", "--porcelain", "--", *paths], ctx.root).splitlines() if line.strip()]
    if lines:
        changed = [line[3:].strip().strip('"') for line in lines]
        summary = f"{len(changed)} path(s) differ from the last commit: {_shown(changed)}"
        fix = (
            "Review and commit them, then merge to the default branch: CI reads workflows, rules and skills from there."
        )
        yield Check("committed", WARN, summary, fix)
    else:
        yield Check("committed", OK, "workflows, rules and Claude files match the last commit")


Checker = Callable[[Context], Iterable[Check]]
CHECKERS: Tuple[Tuple[str, Checker], ...] = (
    ("commands", _commands),
    ("hooks", _hooks),
    ("ignore", _ignore),
    ("skills", _skills),
    ("agents", _agents),
    ("workflows", _workflows),
    ("rules", _rules),
    ("checklist", _checklist),
    ("codeowners", _codeowners),
    ("claude-md", _claude_md),
    ("committed", _committed),
    ("kit-files", lambda ctx: kit_file_checks(ctx.root)),
)


def local_checks(project_dir: Path, env: Mapping[str, str]) -> List[Check]:
    """Every local check, in a fixed order. Never raises."""
    root = Path(project_dir)
    version = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    supported = sys.version_info >= (3, 9)
    checks = [Check("python", OK if supported else FAIL, version, "" if supported else "Use Python 3.9 or newer.")]
    in_git = root.is_dir() and repo_root(root) is not None
    if in_git:
        checks.append(Check("git", OK, "inside a git work tree"))
    else:
        checks.append(Check("git", FAIL, f"{root} is not a git repository", "Run doctor inside the repository."))
    cfg, config_check = _config(root)
    checks.append(config_check)
    ctx = Context(root, cfg, env, in_git)
    for check_id, checker in CHECKERS:
        try:
            checks.extend(checker(ctx))
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            summary = f"the check itself failed ({type(exc).__name__}: {exc})"
            checks.append(Check(check_id, FAIL, summary, "Report this as a bug in the review kit."))
    return checks
