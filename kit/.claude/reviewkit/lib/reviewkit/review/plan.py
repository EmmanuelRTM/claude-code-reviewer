"""Decide whether and how to review a pull request.

Everything here is a pure function of the event, the pull request's facts, the
committed configuration and the state of the last review. The model is never
asked whether a review should run, in which mode, or with which lenses.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from ..config import Config
from .commands import parse_review_command
from .state import PriorFinding, ReviewState

SUBSCRIBED_LABEL = "claude-review:always"
NEEDS_HUMAN_LABEL = "needs-human"
ROUND_LABEL_PREFIX = "review-round:"
WRITE_PERMISSIONS = ("admin", "maintain", "write")
SMALL_CHANGE_LINES = 50

BASE_LENSES = ("correctness", "contracts", "tests-evidence")
ALL_LENSES = BASE_LENSES + ("security", "cross-file")

# Check-run titles for a kept result match the managed service, so scripts keyed on them carry over.
TITLE_UNCHANGED = "Code review skipped — no new changes"
TITLE_NOT_REPEATED = "Code review not repeated — earlier findings still open"
TITLE_PAUSED = "Code review paused: needs a human"
TITLE_TOO_LARGE = "Code review skipped: the diff is too large"
TITLE_NOT_CONFIGURED = "Code review not configured"
TITLE_SKIPPED_TIER = "Code review skipped: risk tier not reviewed automatically"


@dataclass(frozen=True)
class Trigger:
    kind: str  # pull_request | command | dispatch | ignored
    action: str = ""
    command: str = ""
    pr_number: int = 0
    actor: str = ""
    actor_type: str = ""
    dry_run: bool = False

    @property
    def manual(self) -> bool:
        return self.kind in ("command", "dispatch")


@dataclass(frozen=True)
class PullFacts:
    number: int
    open: bool
    draft: bool
    fork: bool
    author: str
    author_type: str
    base_ref: str
    base_sha: str
    head_sha: str
    head_ref: str
    labels: tuple = ()


@dataclass(frozen=True)
class Plan:
    run: bool
    reason: str
    pr_number: int = 0
    head_sha: str = ""
    base_sha: str = ""
    base_ref: str = ""
    merge_base: str = ""
    mode: str = ""
    round: int = 0
    level: str = ""
    lenses: tuple = ()
    publish: str = ""
    manual: bool = False
    patch: str = ""
    last_reviewed_sha: str = ""
    check_title: str = ""
    check_summary: str = ""
    label_add: tuple = ()
    label_remove: tuple = ()
    reply: str = ""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_trigger(event_name: str, event: Mapping[str, Any], env: Mapping[str, str]) -> Trigger:
    if event_name in ("pull_request_target", "pull_request"):
        number = (event.get("pull_request") or {}).get("number")
        if isinstance(number, int):
            return Trigger(kind="pull_request", action=str(event.get("action") or ""), pr_number=number)
    elif event_name == "issue_comment":
        issue = event.get("issue") or {}
        comment = event.get("comment") or {}
        command = parse_review_command(comment.get("body")) if event.get("action") == "created" else None
        if command and issue.get("pull_request") is not None and isinstance(issue.get("number"), int):
            user = comment.get("user") or {}
            return Trigger(
                kind="command",
                command=command,
                pr_number=issue["number"],
                actor=str(user.get("login") or ""),
                actor_type=str(user.get("type") or ""),
            )
    elif event_name == "workflow_dispatch":
        raw = str(env.get("INPUT_PR_NUMBER") or "").strip()
        if raw.isdigit() and int(raw) > 0:
            dry_run = _truthy(env.get("INPUT_DRY_RUN", "true"))
            return Trigger(kind="dispatch", pr_number=int(raw), dry_run=dry_run)
    return Trigger(kind="ignored")


def pull_facts(pr: Mapping[str, Any]) -> PullFacts:
    base, head, user = pr.get("base") or {}, pr.get("head") or {}, pr.get("user") or {}
    base_repo = (base.get("repo") or {}).get("full_name")
    head_repo = (head.get("repo") or {}).get("full_name")
    return PullFacts(
        number=int(pr.get("number") or 0),
        open=pr.get("state") == "open",
        draft=pr.get("draft") is True,
        fork=not head_repo or head_repo != base_repo,
        author=str(user.get("login") or ""),
        author_type=str(user.get("type") or ""),
        base_ref=str(base.get("ref") or ""),
        base_sha=str(base.get("sha") or ""),
        head_sha=str(head.get("sha") or ""),
        head_ref=str(head.get("ref") or ""),
        labels=tuple(label.get("name") for label in pr.get("labels") or [] if isinstance(label, dict)),
    )


def plan_lenses(level: str, changed_lines: int, high_risk: bool) -> tuple:
    """Which finder lenses to dispatch. Size never raises the depth; risk does."""
    if level == "quick":
        return ("all",)
    if level == "deep":
        return ALL_LENSES
    if changed_lines < SMALL_CHANGE_LINES and not high_risk:
        return ("all",)
    return BASE_LENSES + (("security",) if high_risk else ())


def level_for(cfg: Config, high_risk: bool) -> str:
    """The level a change is reviewed at. Risk decides it: a high-risk path gets the deeper level."""
    return cfg.review.high_risk_level if high_risk else cfg.review.level


def lens_assignments(cfg: Config, lenses: Sequence[str]) -> list:
    """`[{"id", "model"}]` per lens. Lenses differ in model as well as in focus (independent generation)."""
    models = cfg.review.models
    special = {"tests-evidence": models.finder_light, "security": models.finder_deep}
    return [{"id": lens, "model": special.get(lens, models.finder)} for lens in lenses]


def _skip(reason: str, pull: PullFacts, **extra: Any) -> Plan:
    return Plan(
        run=False,
        reason=reason,
        pr_number=pull.number,
        head_sha=pull.head_sha,
        base_sha=pull.base_sha,
        base_ref=pull.base_ref,
        **extra,
    )


def precheck(
    trigger: Trigger,
    pull: PullFacts,
    cfg: Config,
    state: Optional[ReviewState],
    has_token: bool,
    actor_permission: str,
) -> Optional[Plan]:
    """Return a terminal plan when no review should start, else None."""
    review = cfg.review
    if trigger.kind == "ignored":
        return Plan(run=False, reason="ignored")
    if trigger.kind == "command" and (trigger.actor_type == "Bot" or actor_permission not in WRITE_PERMISSIONS):
        return _skip("not_permitted", pull)
    if not pull.open:
        return _skip("closed", pull)
    if trigger.command == "stop":
        if review.trigger == "every_push":
            reply = (
                "This repository reviews every push, so reviews cannot be stopped with a comment. "
                "Convert the pull request to a draft to pause them."
            )
            return _skip("unsubscribed", pull, reply=reply)
        reply = "Push-triggered reviews are off for this pull request. Comment `@claude review` to request one."
        return _skip("unsubscribed", pull, reply=reply, label_remove=(SUBSCRIBED_LABEL,))
    if not has_token:
        return _skip(
            "missing_token",
            pull,
            check_title=TITLE_NOT_CONFIGURED,
            check_summary=(
                "The `CLAUDE_CODE_OAUTH_TOKEN` repository secret is not set, so no review ran. "
                "Generate a token with `claude setup-token` and add it under Settings, Secrets and variables, Actions."
            ),
        )
    if trigger.kind != "pull_request":
        return None
    if pull.draft:
        return _skip("draft", pull)
    if pull.fork:
        return _skip("fork", pull)
    if review.skip_bot_authors and pull.author_type == "Bot":
        return _skip("bot_author", pull)
    subscribed = SUBSCRIBED_LABEL in pull.labels
    if review.trigger == "manual" and not subscribed:
        return _skip("trigger_policy", pull)
    if review.trigger == "once" and trigger.action == "synchronize" and not subscribed:
        return _skip("trigger_policy", pull)
    if state and state.escalated and NEEDS_HUMAN_LABEL in pull.labels and review.after_escalation == "pause":
        return _skip(
            "paused",
            pull,
            check_title=TITLE_PAUSED,
            check_summary=(
                f"Important findings were still open after {review.max_rounds} review rounds, so automatic reviews "
                f"are paused. Reconsider the approach with a person, then remove the `{NEEDS_HUMAN_LABEL}` label "
                "or comment `@claude review` to resume."
            ),
        )
    return None


def finalize(
    trigger: Trigger,
    pull: PullFacts,
    cfg: Config,
    state: Optional[ReviewState],
    threads: Sequence[PriorFinding],
    merge_base: str,
    patch: str,
    stats: Mapping[str, int],
    high_risk: bool,
    tier: str = "",
) -> Plan:
    """Decide mode, round and lenses once the diff is known.

    `tier` is the pull request's risk tier (paths, raised by a `risk:*` label);
    it only matters when the repository chose not to review some tiers automatically.
    """
    review = cfg.review
    common = {"merge_base": merge_base, "patch": patch}
    if not stats.get("files") or not stats.get("diff_bytes"):
        return _skip("empty_diff", pull, **common)
    asked_for = trigger.manual or SUBSCRIBED_LABEL in pull.labels
    if tier in review.skip_risk_tiers and not asked_for:
        return _skip(
            "risk_tier",
            pull,
            check_title=TITLE_SKIPPED_TIER,
            check_summary=(
                f"This change is in the {tier} risk tier, which this repository does not review automatically "
                "(`review.skip_risk_tiers`). Comment `@claude review` to request a review."
            ),
            **common,
        )
    if stats["diff_bytes"] > review.max_diff_bytes:
        return _skip(
            "too_large",
            pull,
            check_title=TITLE_TOO_LARGE,
            check_summary=(
                f"The diff is {stats['diff_bytes']:,} bytes across {stats['files']} files; the limit is "
                f"{review.max_diff_bytes:,} (`review.max_diff_bytes`). Split the change, or ask a person to review it."
            ),
            **common,
        )
    if not trigger.manual and state and state.patch and state.patch == patch:
        open_ids = {thread.id for thread in threads if not thread.resolved}
        still_open = [identifier for identifier in state.open_important if identifier in open_ids]
        resolved_since = len(still_open) < len(state.open_important)
        if not resolved_since:
            if still_open:
                return _skip(
                    "unchanged_open_findings",
                    pull,
                    check_title=TITLE_NOT_REPEATED,
                    check_summary=(
                        "This push adds and removes the same lines as the last reviewed commit, and Important "
                        "findings from that review are still open. Push a fix, or resolve the thread and push again."
                    ),
                    **common,
                )
            return _skip(
                "unchanged",
                pull,
                check_title=TITLE_UNCHANGED,
                check_summary="This push adds and removes the same lines as the last reviewed commit.",
                **common,
            )

    escalated_continue = bool(state and state.escalated and review.after_escalation == "continue")
    if trigger.manual or state is None or not state.head:
        mode = "full"
    else:
        mode = "escalation" if escalated_continue else "incremental"
    changed_lines = int(stats.get("additions", 0)) + int(stats.get("deletions", 0))
    subscribe = trigger.command == "always" and not pull.fork
    return Plan(
        run=True,
        reason="run",
        pr_number=pull.number,
        head_sha=pull.head_sha,
        base_sha=pull.base_sha,
        base_ref=pull.base_ref,
        mode=mode,
        round=(state.round if state else 0) + 1,
        level=level_for(cfg, high_risk),
        lenses=plan_lenses(level_for(cfg, high_risk), changed_lines, high_risk),
        publish="dry_run" if trigger.kind == "dispatch" and trigger.dry_run else review.publish,
        manual=trigger.manual,
        last_reviewed_sha=state.head if state and mode != "full" else "",
        label_add=(SUBSCRIBED_LABEL,) if subscribe else (),
        **common,
    )


def plan_to_json(plan: Plan) -> str:
    return json.dumps(dataclasses.asdict(plan), separators=(",", ":"), sort_keys=True)


def plan_from_json(text: str) -> Optional[Plan]:
    try:
        data = json.loads(text or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    names = {f.name for f in dataclasses.fields(Plan)}
    values = {key: tuple(value) if isinstance(value, list) else value for key, value in data.items() if key in names}
    try:
        return Plan(**values)
    except TypeError:
        return None
