"""Publish a review to the pull request.

This is the only code that writes to GitHub for the reviewer, and it runs in a
job that never talked to the model. A review is always a `COMMENT`: it never
approves and never requests changes, and its check run always concludes
`neutral`. Whether findings block a merge is the repository's decision
(conversation resolution, or `review.publish: gate`).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from .. import labels as label_ops
from .. import markers
from ..config import Config
from ..github.client import GitHubError, GitHubPort, split_repo
from . import render
from .findings import Outcome, severity_counts
from .plan import NEEDS_HUMAN_LABEL, ROUND_LABEL_PREFIX, Plan
from .state import STATE_KIND, PriorFinding, ReviewState, is_bot

# `review-round:N` labels are created as needed; every other label is styled in `labels.KIT_LABELS`.
ROUND_LABEL = label_ops.LabelSpec("ededed", "Number of automated review rounds so far")

_RESOLVE_THREAD = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }"


@dataclass
class PublishResult:
    exit_code: int = 0
    open_important: int = 0
    escalated: bool = False
    summary_markdown: str = ""
    warnings: List[str] = field(default_factory=list)


class _Writer:
    """GitHub writes for one pull request; a failed side write becomes a warning, not a lost review."""

    def __init__(self, gh: GitHubPort, repo: str, number: int, warnings: List[str]) -> None:
        self.gh = gh
        self.owner, self.repo = split_repo(repo)
        self.number = number
        self.warnings = warnings
        self.base = f"/repos/{self.owner}/{self.repo}"

    def attempt(self, what: str, action: Callable[[], object]) -> bool:
        try:
            action()
            return True
        except GitHubError as exc:
            self.warnings.append(f"{what} failed: {exc.status} {exc.message[:200]}")
            return False

    def check_run(self, name: str, head_sha: str, output: render.CheckOutput) -> None:
        body = {
            "name": name,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "neutral",
            "output": {"title": output.title, "summary": output.summary, "text": output.text},
        }
        if output.annotations:
            body["output"]["annotations"] = output.annotations
        self.attempt("check run", lambda: self.gh.post(f"{self.base}/check-runs", body))

    def add_label(self, name: str) -> None:
        spec = label_ops.KIT_LABELS.get(name, ROUND_LABEL)
        try:
            label_ops.ensure_label(self.gh, self.owner, self.repo, name, spec.color, spec.description)
        except GitHubError:
            pass  # we may not be allowed to create labels: adding it below decides
        self.attempt(f"label {name}", lambda: label_ops.add_label(self.gh, self.owner, self.repo, self.number, name))

    def remove_label(self, name: str) -> None:
        self.attempt(
            f"removing label {name}",
            lambda: label_ops.remove_label(self.gh, self.owner, self.repo, self.number, name),
        )

    def comment(self, body: str) -> None:
        self.attempt("comment", lambda: self.gh.post(f"{self.base}/issues/{self.number}/comments", {"body": body}))

    def close_thread(self, thread: PriorFinding, reply: Optional[str]) -> None:
        if reply and thread.comment_id is not None:
            path = f"{self.base}/pulls/{self.number}/comments/{thread.comment_id}/replies"
            self.attempt("thread reply", lambda: self.gh.post(path, {"body": reply}))
        if thread.thread_id:
            self.attempt("resolving a thread", lambda: self.gh.graphql(_RESOLVE_THREAD, {"id": thread.thread_id}))


def _inline_comments(outcome: Outcome, round_number: int) -> List[dict]:
    comments = []
    for item in outcome.posted:
        if not item.inline:
            continue
        comment = {
            "path": item.comment_path,
            "line": item.comment_line,
            "side": "RIGHT",
            "body": render.render_comment(item, round_number),
        }
        if item.comment_start_line:
            comment.update({"start_line": item.comment_start_line, "start_side": "RIGHT"})
        comments.append(comment)
    return comments


def _without_inline(outcome: Outcome) -> Outcome:
    flat = [
        dataclasses.replace(item, inline=False, comment_path=None, comment_line=None, comment_start_line=None)
        for item in outcome.posted
    ]
    return dataclasses.replace(outcome, posted=flat)


def _tuning_notes(outcome: Outcome) -> str:
    """Candidates that did not make it, for whoever tunes REVIEW.md and the reviewer."""
    parts: List[str] = []
    if outcome.dropped:
        lines = [
            f"- {item.get('title') or '(untitled)'} (`{item.get('path')}`): {item.get('reason')}"
            for item in outcome.dropped
        ]
        parts.append("#### Dropped: could not be verified against the code\n\n" + "\n".join(lines))
    if outcome.rejected:
        lines = [f"- {item['title']} (`{item['path']}:{item['line']}`): {item['reason']}" for item in outcome.rejected]
        parts.append("#### Rejected by the verification step\n\n" + "\n".join(lines))
    coverage = outcome.coverage or {}
    if coverage.get("candidates") is not None:
        applied = ", ".join(coverage.get("checklist_items") or []) or "none"
        parts.append(
            f"Candidates: {coverage.get('candidates')}, confirmed: {coverage.get('confirmed')}, "
            f"posted: {len(outcome.posted)}. Checklist items applied: {applied}."
        )
    return "\n\n".join(parts)


def _upsert_shadow_comment(writer: _Writer, bot_login: str, body: str) -> None:
    try:
        existing = writer.gh.get_all(f"{writer.base}/issues/{writer.number}/comments")
    except GitHubError:
        existing = []
    mine = [c for c in existing if is_bot(c.get("user"), bot_login) and markers.has(STATE_KIND, c.get("body") or "")]
    if mine:
        path = f"{writer.base}/issues/comments/{mine[-1]['id']}"
        writer.attempt("status comment", lambda: writer.gh.patch(path, {"body": body}))
    else:
        writer.comment(body)


def _apply_plan_labels(writer: _Writer, plan: Plan, labels: Sequence[str]) -> None:
    for name in plan.label_remove:
        if name in labels:
            writer.remove_label(name)
    for name in plan.label_add:
        if name not in labels:
            writer.add_label(name)


def _publish_skip(writer: _Writer, plan: Plan, cfg: Config, labels: Sequence[str]) -> PublishResult:
    _apply_plan_labels(writer, plan, labels)
    if plan.reply:
        writer.comment(plan.reply)
    if plan.check_title and plan.head_sha:
        output = render.render_skip_check(plan.check_title, plan.check_summary, with_counts=plan.reason == "unchanged")
        writer.check_run(cfg.review.check_name, plan.head_sha, output)
    summary = f"No review ran ({plan.reason})." + (f" {plan.check_summary}" if plan.check_summary else "")
    return PublishResult(summary_markdown=summary, warnings=writer.warnings)


def _publish_failure(writer: _Writer, plan: Plan, cfg: Config, reason: str, run_url: str) -> PublishResult:
    output = render.render_failure_check(reason, run_url)
    if plan.publish != "dry_run":
        writer.check_run(cfg.review.check_name, plan.head_sha, output)
    # No verdict is not a pass: a gate fails closed, an advisory review stays neutral.
    return PublishResult(
        exit_code=1 if plan.publish == "gate" else 0, summary_markdown=output.summary, warnings=writer.warnings
    )


def _post_review(writer: _Writer, plan: Plan, outcome: Outcome, body_for: Callable[[Outcome], str]) -> Outcome:
    """Post the review; if GitHub rejects an inline position, post again with everything in the summary."""
    path = f"{writer.base}/pulls/{writer.number}/reviews"
    payload = {"commit_id": plan.head_sha, "event": "COMMENT", "body": body_for(outcome)}
    comments = _inline_comments(outcome, plan.round)
    if comments:
        payload["comments"] = comments
    try:
        writer.gh.post(path, payload)
        return outcome
    except GitHubError as exc:
        if exc.status != 422 or not comments:
            writer.warnings.append(f"posting the review failed: {exc.status} {exc.message[:200]}")
            return outcome
    flat = _without_inline(outcome)
    retry = {"commit_id": plan.head_sha, "event": "COMMENT", "body": body_for(flat)}
    writer.attempt("posting the review", lambda: writer.gh.post(path, retry))
    return flat


def _publish_review(
    writer: _Writer,
    plan: Plan,
    cfg: Config,
    outcome: Outcome,
    threads: Sequence[PriorFinding],
    labels: Sequence[str],
    run_url: str,
    now: str,
) -> PublishResult:
    review = cfg.review
    closing = {key: info for key, info in outcome.prior.items() if info.get("status") in ("fixed", "withdrawn")}
    still_open = [thread for thread in threads if not thread.resolved and thread.id not in closing]
    open_important = tuple(
        [item.id for item in outcome.posted if item.severity == "important"]
        + [thread.id for thread in still_open if thread.severity == "important"]
    )
    prior_counts = severity_counts(still_open)
    new_counts = severity_counts(outcome.posted)
    open_counts = {key: new_counts[key] + prior_counts[key] for key in new_counts}
    escalated = plan.round >= review.max_rounds and bool(open_important)
    state = ReviewState(
        round=plan.round,
        head=plan.head_sha,
        base=plan.merge_base,
        patch=plan.patch,
        level=plan.level,
        mode=plan.mode,
        escalated=escalated,
        open_important=open_important,
        counts=open_counts,
        ts=now,
    )

    def body_for(current: Outcome) -> str:
        return render.render_review_body(current, plan, state, prior_counts, escalated, run_url, review.max_rounds)

    result = PublishResult(
        exit_code=1 if plan.publish == "gate" and open_important else 0,
        open_important=len(open_important),
        escalated=escalated,
        warnings=writer.warnings,
    )
    sections = [body_for(outcome)]
    if plan.publish in ("dry_run", "shadow"):
        # Nothing lands on the diff in these modes, so the run summary carries the findings in full.
        sections += [
            f"`{item.path}:{item.line}`\n\n{render.render_comment(item, plan.round)}" for item in outcome.posted
        ]
    elif outcome.posted:
        sections.append(render.findings_table(outcome.posted))
    sections.append(_tuning_notes(outcome))
    result.summary_markdown = markers.strip("\n\n".join(part for part in sections if part)).rstrip()
    if plan.publish == "dry_run":
        return result

    if plan.publish == "shadow":
        _upsert_shadow_comment(
            writer, review.bot_login, render.render_shadow_comment(plan, open_counts, run_url, state)
        )
    else:
        outcome = _post_review(writer, plan, outcome, body_for)
        by_id = {thread.id: thread for thread in threads}
        for identifier, info in closing.items():
            thread = by_id.get(identifier)
            if thread is not None and not thread.resolved:
                writer.close_thread(thread, render.prior_reply(info["status"], info.get("note", ""), plan.head_sha))

    writer.check_run(review.check_name, plan.head_sha, render.render_check(outcome, plan, open_counts, still_open))

    wanted = f"{ROUND_LABEL_PREFIX}{plan.round}"
    for name in labels:
        if name.startswith(ROUND_LABEL_PREFIX) and name != wanted:
            writer.remove_label(name)
    if wanted not in labels:
        writer.add_label(wanted)
    if escalated and NEEDS_HUMAN_LABEL not in labels:
        writer.add_label(NEEDS_HUMAN_LABEL)
    _apply_plan_labels(writer, plan, labels)
    return result


def publish(
    gh: GitHubPort,
    repo: str,
    plan: Plan,
    cfg: Config,
    outcome: Optional[Outcome],
    threads: Sequence[PriorFinding],
    labels: Sequence[str],
    *,
    review_outcome: str,
    run_url: str,
    now: str,
) -> PublishResult:
    writer = _Writer(gh, repo, plan.pr_number, [])
    if not plan.run:
        return _publish_skip(writer, plan, cfg, labels)
    if review_outcome != "success" or outcome is None:
        return _publish_failure(writer, plan, cfg, "the review session did not return a result", run_url)
    if not outcome.valid:
        return _publish_failure(writer, plan, cfg, outcome.error or "the result was not usable", run_url)
    return _publish_review(writer, plan, cfg, outcome, threads, labels, run_url, now)
