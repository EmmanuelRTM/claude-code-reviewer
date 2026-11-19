"""Gate and report commands: `risk evaluate`, `issue intake`, `checklist lint`, `metrics report`.

The handlers are thin: they read the environment and the event file, call the
pure logic in `risk`, `approvals`, `issues`, `checklist` and `metrics`, and
make the GitHub calls. Workflows run them from a checkout of the trusted base
branch; none of them executes anything from a pull request.

Text that people wrote (titles, bodies, comments, branch names) is never
printed, written to an output or sent back to GitHub from here.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import approvals, checklist, ci, issues, labels, metrics, risk
from .config import Config, ConfigError, load_config
from .github.client import GitHubError, client_from_env, split_repo
from .metrics_report import render_report, report_title

Handler = Callable[[argparse.Namespace], int]

MAX_DAYS = 3650
LISTING_LIMIT = 3000  # most reviews or comments the gate will read for one pull request
MAX_ISSUE_BODY = 60_000  # GitHub refuses issue bodies beyond 65536 characters
METRICS_LABEL = "review-metrics"
_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")


class CommandError(Exception):
    """A command cannot run: its environment or its input is missing or malformed."""


def _guard(run: Handler) -> Handler:
    """Turn the failures a command expects into one line on stderr and exit status 1."""

    @functools.wraps(run)
    def handler(args: argparse.Namespace) -> int:
        try:
            return run(args)
        except (GitHubError, ConfigError, CommandError) as exc:
            print(f"error: {' '.join(str(exc).split())}", file=sys.stderr)
            return 1

    return handler


def _repository(explicit: Optional[str] = None) -> Tuple[str, str]:
    name = explicit or os.environ.get("GITHUB_REPOSITORY", "")
    if not name:
        raise CommandError("no repository: set GITHUB_REPOSITORY (or pass --repo OWNER/NAME where available)")
    try:
        return split_repo(name)
    except ValueError:
        raise CommandError("the repository must be given as OWNER/NAME") from None


def _config() -> Config:
    """Configuration of the project in the current directory (the trusted checkout)."""
    return load_config(Path.cwd()).config


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# -- risk evaluate ------------------------------------------------------------


def _pull_number(event_name: str, event: Dict[str, Any]) -> Optional[int]:
    """The pull request an event asks about, or None when the event is not for this gate."""
    if event_name == "pull_request_target":
        source = event.get("pull_request")
    elif event_name == "issue_comment":
        source = event.get("issue")
        comment = event.get("comment")
        body = comment.get("body") if isinstance(comment, dict) else None
        if not isinstance(source, dict) or "pull_request" not in source or not approvals.is_gate_command(body):
            return None
    else:
        return None
    number = source.get("number") if isinstance(source, dict) else None
    if not isinstance(number, int) or isinstance(number, bool):
        raise CommandError("the event does not carry a pull request number")
    return number


def _sync_risk_label(gh: Any, owner: str, repo: str, number: int, tier: str, current: List[str]) -> None:
    wanted = risk.tier_label(tier)
    if not labels.has_label(current, wanted):
        labels.ensure_kit_label(gh, owner, repo, wanted)
    labels.set_exclusive_label(gh, owner, repo, number, risk.LABEL_PREFIX, wanted, current)


def _list_complete(gh: Any, path: str, what: str) -> List[Any]:
    """Every entry of a listing, or an error: the gate does not decide on part of the evidence.

    A later review can withdraw an earlier approval, so a listing that was cut
    short could count an approval that no longer stands.
    """
    items = gh.get_all(path, limit=LISTING_LIMIT + 1)
    if len(items) > LISTING_LIMIT:
        raise CommandError(f"more than {LISTING_LIMIT} {what} on this pull request; refusing to decide on a part")
    return items


def _risk_summary(number: int, head_sha: str, result: approvals.ApprovalResult) -> str:
    """The step summary: the decision, who approved, and how to approve while approvals are missing.

    Built from numbers, fixed wording, the commit sha and logins that pass a
    strict pattern; nothing a person wrote in the pull request is repeated.
    """
    shown = [f"`{login}`" for login in result.approvers if _SAFE_LOGIN.match(login)]
    lines = [
        "### Risk approval",
        "",
        "| Pull request | Risk tier | Approvals required | Approvals on this commit | State |",
        "| --- | --- | --- | --- | --- |",
        f"| #{number} | {result.tier} | {result.required} | {len(result.approvers)} | {result.state} |",
        "",
        result.description,
        "",
        f"Commit `{head_sha[:7]}` approved by: {', '.join(shown) if shown else 'nobody yet'}.",
    ]
    if result.state != "success":
        lines += [
            "",
            "An approval covers one commit and counts only from people with write access. "
            "Agents and bots never count. To approve this commit:",
            "",
            "- approve the pull request in the GitHub interface, then comment `/recheck`; or",
            "- post the comment below. The author of the pull request cannot approve it in the interface, "
            "so this is how the author vouches for commits an agent pushed.",
            "",
            "```",
            f"/approve {head_sha}",
            "```",
            "",
            "The command must be the first line of a new comment. "
            "A comment that was edited after posting does not count.",
        ]
    return "\n".join(lines)


def _risk_evaluate(_args: argparse.Namespace) -> int:
    number = _pull_number(os.environ.get("GITHUB_EVENT_NAME", ""), ci.load_event())
    if number is None:
        print("risk evaluate: not a pull request event or an /approve or /recheck comment; nothing to do")
        return 0
    owner, repo = _repository()
    cfg = _config()
    gh = client_from_env()
    base = f"/repos/{owner}/{repo}"

    pull = gh.get(f"{base}/pulls/{number}")
    if not isinstance(pull, dict) or pull.get("state") != "open":
        return 0
    if (pull.get("base") or {}).get("ref") != cfg.default_branch:
        ci.notice(f"risk approval gates pull requests into {cfg.default_branch} only; #{number} is not gated")
        return 0
    head_sha = str((pull.get("head") or {}).get("sha") or "").lower()
    if not _FULL_SHA.match(head_sha):
        raise CommandError(f"pull request #{number} has no readable head commit")

    files = gh.get_all(f"{base}/pulls/{number}/files")
    current = labels.label_names(pull.get("labels"))
    tier = risk.final_tier(risk.tier_for_pull(files, pull.get("changed_files"), cfg.risk), current)
    _sync_risk_label(gh, owner, repo, number, tier, current)

    result = approvals.evaluate_approvals(
        reviews=_list_complete(gh, f"{base}/pulls/{number}/reviews", "reviews"),
        comments=_list_complete(gh, f"{base}/issues/{number}/comments", "comments"),
        head_sha=head_sha,
        author=str((pull.get("user") or {}).get("login") or ""),
        tier=tier,
        required=risk.required_approvals(tier, cfg.risk),
        agent_logins=cfg.risk.agent_logins,
        permission_of=approvals.fetch_permission(gh, owner, repo),
        author_can_approve=cfg.risk.author_can_approve,
    )
    siblings = approvals.open_pulls_for_commit(gh, owner, repo, head_sha)
    result = approvals.apply_sibling_guard(result, siblings, number, head_sha)
    approvals.post_status(gh, owner, repo, head_sha, result, cfg.risk.status_context, ci.run_url())

    ci.append_summary(_risk_summary(number, head_sha, result))
    ci.set_output("tier", result.tier)
    ci.set_output("required", result.required)
    ci.set_output("approvals", len(result.approvers))
    ci.set_output("state", result.state)
    print(f"risk evaluate: #{number} is {result.state}: {result.description}")
    return 0


# -- issue intake -------------------------------------------------------------


def _issue_intake(_args: argparse.Namespace) -> int:
    event = ci.load_event()
    issue = event.get("issue")
    wanted = os.environ.get("GITHUB_EVENT_NAME", "") == "issues" and event.get("action") in ("opened", "edited")
    if not wanted or not isinstance(issue, dict):
        print("issue intake: not an opened or edited issue; nothing to do")
        return 0
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise CommandError("the event does not carry an issue number")
    owner, repo = _repository()
    outcome = issues.apply_intake(client_from_env(), owner, repo, issue)
    if outcome is None:
        print("issue intake: this is a pull request, not an issue; nothing to do")
        return 0
    # Only fixed words and counts are printed; nothing the issue says.
    parts = [
        f"risk tier {outcome.result.risk or 'not chosen'}",
        f"acceptance criteria {'present' if outcome.result.has_acceptance_criteria else 'missing'}",
        f"{_plural(len(outcome.added), 'label')} added",
        f"{_plural(len(outcome.removed), 'label')} removed",
        f"reminder {'posted' if outcome.commented else 'not posted'}",
    ]
    print(f"issue intake: #{number}: {', '.join(parts)}")
    return 0


# -- checklist lint -----------------------------------------------------------


def _checklist_lint(args: argparse.Namespace) -> int:
    shown = args.path or _config().review.checklist_path
    try:
        text = Path(shown).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CommandError(f"cannot read the checklist at {shown}: {type(exc).__name__}") from None
    found = checklist.lint_checklist(text)
    for issue in found:
        print(f"{issue.level}: line {issue.line}: {issue.message}")
    errors = sum(1 for issue in found if issue.level == "error")
    items = len(checklist.parse_checklist(text))
    print(f"{shown}: {_plural(items, 'item')}, {_plural(errors, 'error')}, {_plural(len(found) - errors, 'warning')}")
    return 1 if errors else 0


# -- metrics report -----------------------------------------------------------


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _days(text: str) -> int:
    """`--days` value; an empty string (a scheduled run has no input) means the default."""
    if not text.strip():
        return metrics.DEFAULT_DAYS
    try:
        days = int(text.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("expected a whole number of days") from None
    if not 1 <= days <= MAX_DAYS:
        raise argparse.ArgumentTypeError(f"expected between 1 and {MAX_DAYS} days")
    return days


def _checklist_items(cfg: Config) -> List[checklist.ChecklistItem]:
    """Rows of the project's checklist; a missing or unreadable file simply has none."""
    try:
        text = (Path.cwd() / cfg.review.checklist_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return checklist.parse_checklist(text)


def _fit_issue(report: str) -> str:
    if len(report) <= MAX_ISSUE_BODY:
        return report
    return report[:MAX_ISSUE_BODY].rstrip() + "\n\nThis report was cut to fit into an issue.\n"


def _metrics_report(args: argparse.Namespace) -> int:
    owner, repo = _repository(args.repo)
    cfg = _config()
    start, end = metrics.window(args.days, _today())
    gh = client_from_env()
    limit = metrics.MAX_PULLS
    pulls = metrics.collect_pulls(gh, owner, repo, start, limit)
    notes = []
    if len(pulls) >= limit:
        notes.append(
            f"This report covers the first {limit} merged pull requests found, newest first; the window may hold more."
        )
    data = metrics.compute_metrics(pulls, cfg.risk.agent_logins)
    report = render_report(data, start, end, _checklist_items(cfg), notes)
    sys.stdout.write(report)
    ci.append_summary(report)
    if args.post_issue:
        labels.ensure_kit_label(gh, owner, repo, METRICS_LABEL)
        created = gh.post(
            f"/repos/{owner}/{repo}/issues",
            {"title": report_title(start, end), "body": _fit_issue(report), "labels": [METRICS_LABEL]},
        )
        number = created.get("number") if isinstance(created, dict) else None
        where = f" as issue #{number}" if isinstance(number, int) else ""
        print(f"metrics report: posted{where}", file=sys.stderr)
    return 0


# -- registration -------------------------------------------------------------


def _group(subparsers: Any, name: str, help_text: str) -> Any:
    """A command group (`rk <name> <subcommand>`) whose subcommand is required."""
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    return parser.add_subparsers(dest=f"{name}_command", metavar="<subcommand>", required=True)


def register(subparsers: Any) -> None:
    """Register this module's subcommands."""
    risk_group = _group(subparsers, "risk", "risk tiers and the tiered human-approval commit status")
    evaluate = risk_group.add_parser(
        "evaluate",
        help="label the pull request in the event with its risk tier and post the approval status",
    )
    evaluate.set_defaults(handler=_guard(_risk_evaluate))

    issue_group = _group(subparsers, "issue", "issue intake (used by the workflow)")
    intake = issue_group.add_parser(
        "intake",
        help="label the issue in the event from its form answers; ask once for acceptance criteria",
    )
    intake.set_defaults(handler=_guard(_issue_intake))

    checklist_group = _group(subparsers, "checklist", "the reliability checklist")
    lint = checklist_group.add_parser("lint", help="check that the reliability checklist is well formed")
    lint.add_argument("path", nargs="?", metavar="PATH", help="checklist file (default: review.checklist_path)")
    lint.set_defaults(handler=_guard(_checklist_lint))

    metrics_group = _group(subparsers, "metrics", "review metrics: the monthly report on the review process")
    report = metrics_group.add_parser("report", help="print the review metrics report for the last N days")
    report.add_argument(
        "--days",
        type=_days,
        default=metrics.DEFAULT_DAYS,
        metavar="N",
        help=f"days to look back (default: {metrics.DEFAULT_DAYS})",
    )
    report.add_argument("--post-issue", action="store_true", help="also post the report as an issue")
    report.add_argument("--repo", metavar="OWNER/NAME", help="repository (default: GITHUB_REPOSITORY)")
    report.set_defaults(handler=_guard(_metrics_report))
