"""Per-repository configuration (`.claude/reviewkit.json`).

The file is committed, so it is part of the shared contract and is read from
the protected base branch in CI. Every key is optional; defaults live here.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

CONFIG_RELATIVE_PATH = ".claude/reviewkit.json"

GATE_MODES = ("off", "observe", "enforce")
REVIEW_LEVELS = ("quick", "standard", "deep")
REVIEW_TRIGGERS = ("every_push", "once", "manual")
PUBLISH_MODES = ("shadow", "advisory", "gate")
# A high-risk change is always reviewed; only the lower tiers can be left to people.
SKIPPABLE_TIERS = ("low", "medium")
ESCALATION_POLICIES = ("pause", "continue")
CHECK_NAMES = ("lint", "build", "test")


class ConfigError(Exception):
    """The configuration file is unreadable or holds an invalid value."""


@dataclass(frozen=True)
class Commands:
    """Shell commands that prove the project works. Empty means "not configured"."""

    test: str = ""
    lint: str = ""
    build: str = ""
    start: str = ""
    health_check: str = ""


@dataclass(frozen=True)
class ChecksConfig:
    """What the Stop hook runs before Claude may finish a turn."""

    on_stop: tuple = ("test",)
    timeout_seconds: int = 600
    max_blocks: int = 3
    zero_run_is_failure: bool = True


@dataclass(frozen=True)
class GateConfig:
    """Commit gate: `off`, `observe` (log would-be blocks) or `enforce`."""

    mode: str = "observe"
    max_blocks: int = 2
    require_trailer: bool = True
    exempt_paths: tuple = ("**/*.md", "docs/**")


@dataclass(frozen=True)
class ReviewModels:
    """Models per role. Finders use more than one model so their blind spots differ."""

    lead: str = "opus"
    finder: str = "sonnet"
    finder_light: str = "haiku"  # the tests-evidence lens: pattern checks, kept cheap
    finder_deep: str = "opus"  # the security lens: only dispatched for risky changes
    verifier: str = "opus"


@dataclass(frozen=True)
class ReviewConfig:
    """The CI reviewer. Mirrors the knobs of the managed Code Review service."""

    check_name: str = "Claude PR Review"
    trigger: str = "every_push"
    publish: str = "advisory"
    level: str = "quick"
    high_risk_level: str = "standard"
    max_rounds: int = 3
    after_escalation: str = "pause"
    models: ReviewModels = field(default_factory=ReviewModels)
    effort: str = "medium"
    max_turns: int = 40
    max_diff_bytes: int = 600_000
    max_file_diff_lines: int = 2_000
    max_findings: int = 30
    max_nits: int = 5
    skip_bot_authors: bool = True
    skip_risk_tiers: tuple = ()
    allowed_bots: tuple = ("claude[bot]",)
    bot_login: str = "github-actions"
    instructions_path: str = "REVIEW.md"
    checklist_path: str = "docs/reliability-checklist.md"
    protected_paths: tuple = (
        ".github/**",
        ".claude/**",
        "REVIEW.md",
        "**/REVIEW.md",
        "CLAUDE.md",
        "**/CLAUDE.md",
        "docs/reliability-checklist.md",
    )


@dataclass(frozen=True)
class Approvals:
    low: int = 0
    medium: int = 1
    high: int = 2


@dataclass(frozen=True)
class RiskConfig:
    """Risk tiers decide how many human approvals a pull request needs."""

    high_paths: tuple = (
        ".github/**",
        ".claude/**",
        "REVIEW.md",
        "**/REVIEW.md",
        "CLAUDE.md",
        "**/CLAUDE.md",
        "docs/reliability-checklist.md",
        "**/migrations/**",
        "**/auth/**",
        "**/billing/**",
    )
    low_paths: tuple = ("docs/**", "**/*.md", "**/*.txt", "LICENSE")
    approvals: Approvals = field(default_factory=Approvals)
    status_context: str = "risk-approval"
    agent_logins: tuple = ("claude", "github-actions", "dependabot", "copilot")
    # The person who directed an agent-written change counts as a reviewer of it (`/approve <sha>`).
    author_can_approve: bool = True


@dataclass(frozen=True)
class InteractiveConfig:
    """The `@claude` workflow for issues and pull requests."""

    max_turns: int = 60
    model: str = ""


@dataclass(frozen=True)
class Config:
    version: int = 1
    default_branch: str = "main"
    owners: tuple = ()
    commands: Commands = field(default_factory=Commands)
    checks: ChecksConfig = field(default_factory=ChecksConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    interactive: InteractiveConfig = field(default_factory=InteractiveConfig)


@dataclass(frozen=True)
class LoadedConfig:
    config: Config
    warnings: tuple = ()
    source: Optional[Path] = None


def _build(cls: type, data: Any, where: str, warnings: list) -> Any:
    """Build dataclass `cls` from `data`, type-checking every provided field."""
    if not isinstance(data, dict):
        raise ConfigError(f"{where or 'config'}: expected an object")
    prefix = f"{where}." if where else ""
    known = {f.name: f for f in dataclasses.fields(cls)}
    for key in data:
        if key not in known:
            warnings.append(f"unknown key ignored: {prefix}{key}")
    values: dict = {}
    for name, spec in known.items():
        if name not in data:
            continue
        default = _default_of(spec)
        values[name] = _coerce(data[name], default, f"{prefix}{name}", warnings)
    return cls(**values)


def _default_of(spec: dataclasses.Field[Any]) -> Any:
    if spec.default is not dataclasses.MISSING:
        return spec.default
    return spec.default_factory()  # type: ignore[misc]


def _coerce(value: Any, default: Any, where: str, warnings: list) -> Any:
    if dataclasses.is_dataclass(default):
        return _build(type(default), value, where, warnings)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: expected true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: expected an integer")
        return value
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"{where}: expected a string")
        return value
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{where}: expected a list of strings")
        return tuple(value)
    raise ConfigError(f"{where}: unsupported setting type")  # pragma: no cover


def _require(condition: bool, message: str, errors: list) -> None:
    if not condition:
        errors.append(message)


def validate(cfg: Config) -> list:
    """Return every rule the configuration breaks (empty when valid)."""
    errors: list = []
    _require(cfg.gate.mode in GATE_MODES, f"gate.mode: one of {', '.join(GATE_MODES)}", errors)
    _require(cfg.gate.max_blocks >= 0, "gate.max_blocks: must be 0 or more", errors)
    _require(cfg.checks.timeout_seconds > 0, "checks.timeout_seconds: must be positive", errors)
    _require(cfg.checks.max_blocks >= 0, "checks.max_blocks: must be 0 or more", errors)
    _require(
        all(name in CHECK_NAMES for name in cfg.checks.on_stop),
        f"checks.on_stop: entries must be among {', '.join(CHECK_NAMES)}",
        errors,
    )
    review = cfg.review
    for name in ("level", "high_risk_level"):
        _require(getattr(review, name) in REVIEW_LEVELS, f"review.{name}: one of {', '.join(REVIEW_LEVELS)}", errors)
    _require(review.trigger in REVIEW_TRIGGERS, f"review.trigger: one of {', '.join(REVIEW_TRIGGERS)}", errors)
    _require(review.publish in PUBLISH_MODES, f"review.publish: one of {', '.join(PUBLISH_MODES)}", errors)
    _require(
        review.after_escalation in ESCALATION_POLICIES,
        f"review.after_escalation: one of {', '.join(ESCALATION_POLICIES)}",
        errors,
    )
    for name in ("max_rounds", "max_turns", "max_diff_bytes", "max_file_diff_lines", "max_findings"):
        _require(getattr(review, name) > 0, f"review.{name}: must be positive", errors)
    _require(review.max_nits >= 0, "review.max_nits: must be 0 or more", errors)
    _require(
        all(tier in SKIPPABLE_TIERS for tier in review.skip_risk_tiers),
        f"review.skip_risk_tiers: entries must be among {', '.join(SKIPPABLE_TIERS)}",
        errors,
    )
    _require(bool(review.check_name.strip()), "review.check_name: must not be empty", errors)
    for tier in ("low", "medium", "high"):
        _require(getattr(cfg.risk.approvals, tier) >= 0, f"risk.approvals.{tier}: must be 0 or more", errors)
    _require(cfg.interactive.max_turns > 0, "interactive.max_turns: must be positive", errors)
    return errors


def parse_config(data: Any) -> tuple:
    """Parse already-decoded JSON into `(Config, warnings)`."""
    warnings: list = []
    cfg = _build(Config, data, "", warnings)
    errors = validate(cfg)
    if errors:
        raise ConfigError("; ".join(errors))
    return cfg, tuple(warnings)


def load_config(project_dir: Path) -> LoadedConfig:
    path = Path(project_dir) / CONFIG_RELATIVE_PATH
    if not path.is_file():
        return LoadedConfig(Config())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{CONFIG_RELATIVE_PATH}: cannot read ({exc})") from exc
    cfg, warnings = parse_config(data)
    return LoadedConfig(cfg, warnings, path)


def config_to_dict(cfg: Config) -> dict:
    """Plain-JSON form of a configuration (tuples become lists)."""
    return json.loads(json.dumps(dataclasses.asdict(cfg)))


def effective_gate_mode(cfg: Config, env: Mapping[str, str]) -> str:
    """Committed mode, unless `VERIFY_GATE_MODE` names another valid one."""
    override = env.get("VERIFY_GATE_MODE", "").strip().lower()
    return override if override in GATE_MODES else cfg.gate.mode


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def hooks_disabled(env: Mapping[str, str]) -> bool:
    """Local hooks never run in CI unless explicitly opted in.

    A CI job holds tokens and may check out a pull request's code, so running
    the project's test command from a hook there would execute untrusted code.
    """
    if _truthy(env.get("REVIEWKIT_DISABLE")):
        return True
    in_ci = _truthy(env.get("GITHUB_ACTIONS")) or _truthy(env.get("CI"))
    return in_ci and not _truthy(env.get("REVIEWKIT_HOOKS_IN_CI"))
