"""CLI for the CI reviewer: `review prepare`, `review publish`, `review local-context`, `review schema`.

`prepare` runs before the Claude session and decides everything a rule can
decide; `publish` runs afterwards in a separate job that holds the write
permissions the session never had.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import ci
from .config import ConfigError, load_config
from .fingerprint import FingerprintError, tree_fingerprint
from .github.client import GitHubError, GitHubPort, client_from_env, split_repo
from .gitio import GitError, git, repo_root
from .localstate import utc_now
from .paths import match_any, state_dir
from .review import evals
from .review.context import Workspace, build_context, fetch_commits, has_commit, read_blob
from .review.diffmap import diff_stats, parse_unified_diff, patch_id, unified_diff
from .review.findings import normalize
from .review.plan import (
    Plan,
    finalize,
    level_for,
    parse_trigger,
    plan_from_json,
    plan_lenses,
    plan_to_json,
    precheck,
    pull_facts,
)
from .review.publish import publish
from .review.schema import schema_json
from .review.session import claude_args
from .review.state import fetch_state, fetch_threads
from .risk import final_tier, pr_tier

Fetch = Callable[[Path, list], list]


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _emit(env: Mapping[str, str], plan: Plan, **outputs: str) -> int:
    ci.set_output("run", plan.run, env)
    ci.set_output("plan", plan_to_json(plan), env)
    for name, value in outputs.items():
        ci.set_output(name, value, env)
    detail = f"round {plan.round}, {plan.mode}, lenses: {', '.join(plan.lenses)}" if plan.run else plan.reason
    ci.append_summary(f"**Review plan:** {'run' if plan.run else 'skip'} ({detail})", env)
    return 0


def _workspace(env: Mapping[str, str]) -> Workspace:
    root = env.get("REVIEWKIT_WORKDIR") or (
        os.path.join(env["RUNNER_TEMP"], "reviewkit")
        if env.get("RUNNER_TEMP")
        else tempfile.mkdtemp(prefix="reviewkit-")
    )
    return Workspace(Path(root))


def _permission(gh: GitHubPort, owner: str, repo: str, login: str) -> str:
    try:
        data = gh.get(f"/repos/{owner}/{repo}/collaborators/{login}/permission")
    except GitHubError:
        return "none"
    return str((data or {}).get("permission") or "none")


def prepare(env: Mapping[str, str], gh: GitHubPort, repo_dir: Path, fetch: Fetch = fetch_commits) -> int:
    cfg = load_config(repo_dir).config
    trigger = parse_trigger(env.get("GITHUB_EVENT_NAME", ""), ci.load_event(env), env)
    if trigger.kind == "ignored":
        return _emit(env, Plan(run=False, reason="ignored"))
    repo_name = env.get("GITHUB_REPOSITORY", "")
    owner, name = split_repo(repo_name)
    pr = gh.get(f"/repos/{owner}/{name}/pulls/{trigger.pr_number}")
    pull = pull_facts(pr)
    permission = _permission(gh, owner, name, trigger.actor) if trigger.kind == "command" else ""
    state = fetch_state(gh, owner, name, pull.number, cfg.review.bot_login)
    early = precheck(trigger, pull, cfg, state, _truthy(env.get("HAS_CLAUDE_TOKEN")), permission)
    if early is not None:
        return _emit(env, early)

    threads = fetch_threads(gh, owner, name, pull.number, cfg.review.bot_login)
    compare = gh.get(f"/repos/{owner}/{name}/compare/{pull.base_sha}...{pull.head_sha}")
    merge_base = str(((compare or {}).get("merge_base_commit") or {}).get("sha") or "")
    wanted = [pull.head_sha, merge_base] + ([state.head] if state and state.head else [])
    missing = fetch(repo_dir, wanted)
    if not merge_base or pull.head_sha in missing or merge_base in missing:
        raise GitError(["fetch"], 1, "could not fetch the pull request's commits")

    diff_text = unified_diff(repo_dir, merge_base, pull.head_sha)
    files = parse_unified_diff(diff_text)
    touched = {path for entry in files.values() for path in (entry.path, entry.old_path)}
    high_risk = any(match_any(cfg.risk.high_paths, path) for path in touched)
    plan = finalize(
        trigger,
        pull,
        cfg,
        state,
        threads,
        merge_base,
        patch_id(repo_dir, merge_base, pull.head_sha),
        diff_stats(files, diff_text),
        high_risk,
        final_tier(pr_tier(touched, cfg.risk), pull.labels),
    )
    if not plan.run:
        return _emit(env, plan)
    if plan.mode != "full" and not has_commit(repo_dir, plan.last_reviewed_sha):
        plan = dataclasses.replace(plan, mode="full", last_reviewed_sha="")  # the old head was rewritten away

    workspace = _workspace(env)
    build_context(
        repo=repo_dir,
        workspace=workspace,
        cfg=cfg,
        plan=plan,
        repo_name=repo_name,
        rules_commit=git(["rev-parse", "HEAD"], repo_dir).strip(),
        files=files,
        diff_text=diff_text,
        threads=threads,
        pr_title=str(pr.get("title") or ""),
        pr_body=str(pr.get("body") or ""),
        author=pull.author,
    )
    return _emit(
        env,
        plan,
        context_path=str(workspace.context_file),
        prompt=f"/pr-review {workspace.context_file}",
        claude_args=claude_args(cfg, workspace),
        allowed_bots=",".join(cfg.review.allowed_bots),
    )


def publish_command(env: Mapping[str, str], gh: GitHubPort, repo_dir: Path, fetch: Fetch = fetch_commits) -> int:
    plan = plan_from_json(env.get("PLAN", ""))
    if plan is None or (not plan.run and not (plan.check_title or plan.reply or plan.label_add or plan.label_remove)):
        return 0  # nothing was planned, or a quiet skip
    cfg = load_config(repo_dir).config
    repo_name = env.get("GITHUB_REPOSITORY", "")
    owner, name = split_repo(repo_name)
    pr = gh.get(f"/repos/{owner}/{name}/pulls/{plan.pr_number}")
    pull = pull_facts(pr)
    review_outcome = env.get("REVIEW_OUTCOME", "")
    outcome, threads = None, []
    if plan.run:
        if pull.head_sha != plan.head_sha:
            ci.append_summary(
                f"Review of `{plan.head_sha[:7]}` was superseded by a newer commit and was not published.", env
            )
            return 0
        threads = fetch_threads(gh, owner, name, plan.pr_number, cfg.review.bot_login)
        raw = _parse_result(env.get("REVIEW_RESULT", ""))
        if review_outcome == "success" and raw is not None:
            if fetch(repo_dir, [plan.head_sha, plan.merge_base]):
                raise GitError(["fetch"], 1, "could not fetch the pull request's commits")
            outcome = normalize(
                raw,
                diff=parse_unified_diff(unified_diff(repo_dir, plan.merge_base, plan.head_sha)),
                read_file=lambda path: read_blob(repo_dir, plan.head_sha, path),
                prior_open=[
                    {"id": t.id, "path": t.path, "line": t.line, "severity": t.severity}
                    for t in threads
                    if not t.resolved
                ],
                mode=plan.mode,
                max_findings=cfg.review.max_findings,
                max_nits=cfg.review.max_nits,
                planned_lenses=plan.lenses,
            )
    result = publish(
        gh,
        repo_name,
        plan,
        cfg,
        outcome,
        threads,
        pull.labels,
        review_outcome=review_outcome,
        run_url=ci.run_url(env),
        now=utc_now(),
    )
    ci.append_summary(result.summary_markdown, env)
    for warning in result.warnings:
        ci.warning(warning)
    return result.exit_code


def _parse_result(text: str) -> Optional[dict]:
    try:
        data = json.loads(text or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def local_context(repo_dir: Path, base: Optional[str] = None) -> int:
    """Prepare the same context for the working tree, so the review skill can run before a push."""
    cfg = load_config(repo_dir).config
    candidates = [base] if base else [f"origin/{cfg.default_branch}", cfg.default_branch]
    base_ref = next((ref for ref in candidates if ref and has_commit(repo_dir, ref)), None)
    if base_ref is None:
        print(f"reviewkit: no base to compare against (tried {', '.join(c for c in candidates if c)})", file=sys.stderr)
        return 1
    merge_base = git(["merge-base", "HEAD", base_ref], repo_dir).strip()
    head_tree = tree_fingerprint(repo_dir)
    diff_text = unified_diff(repo_dir, merge_base, head_tree)
    files = parse_unified_diff(diff_text)
    stats = diff_stats(files, diff_text)
    touched = {path for entry in files.values() for path in (entry.path, entry.old_path)}
    high_risk = any(match_any(cfg.risk.high_paths, path) for path in touched)
    plan = Plan(
        run=True,
        reason="local",
        head_sha=head_tree,
        merge_base=merge_base,
        base_ref=base_ref,
        mode="full",
        round=1,
        level=level_for(cfg, high_risk),
        publish="dry_run",
        manual=True,
        lenses=plan_lenses(level_for(cfg, high_risk), stats["additions"] + stats["deletions"], high_risk),
    )
    workspace = Workspace(state_dir(repo_dir) / "review")
    build_context(
        repo=repo_dir,
        workspace=workspace,
        cfg=cfg,
        plan=plan,
        repo_name=repo_dir.name,
        rules_commit=merge_base,
        files=files,
        diff_text=diff_text,
        threads=[],
        local=True,
    )
    print(workspace.context_file)
    return 0


def _guarded(action: Callable[[], int]) -> int:
    try:
        return action()
    except (ConfigError, GitHubError, GitError, FingerprintError, ValueError) as exc:
        ci.error(f"reviewkit: {exc}")
        print(f"reviewkit: {exc}", file=sys.stderr)
        return 1


def eval_command(args, repo_dir: Path) -> int:
    """Score the reviewer on known-bug cases. Runs real sessions on your Claude subscription."""
    cases = evals.load_cases(Path(args.cases))
    if not cases:
        print(f"reviewkit: no cases found under {args.cases} (expected <case>/case.json)", file=sys.stderr)
        return 1
    workdir = Path(tempfile.mkdtemp(prefix="reviewkit-eval-"))
    scores = evals.run_eval(repo_dir, cases, level=args.level, runs=args.runs, workdir=workdir)
    summary = evals.aggregate(scores)
    print(evals.render(scores, summary, args.level, args.runs))
    if args.json:
        payload = {
            "level": args.level,
            "runs": args.runs,
            "summary": summary,
            "scores": [dataclasses.asdict(s) for s in scores],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


def register(subparsers) -> None:
    review = subparsers.add_parser("review", help="the pull request reviewer (used by the workflow)")
    actions = review.add_subparsers(dest="review_command", metavar="<action>")
    here = lambda: repo_root(Path(".").resolve()) or Path(".").resolve()  # noqa: E731

    actions.add_parser("prepare", help="decide whether to review and build the context").set_defaults(
        handler=lambda _args: _guarded(lambda: prepare(os.environ, client_from_env(), here()))
    )
    actions.add_parser("publish", help="validate the result and post it").set_defaults(
        handler=lambda _args: _guarded(lambda: publish_command(os.environ, client_from_env(), here()))
    )
    local = actions.add_parser("local-context", help="build the context for the working tree (prints its path)")
    local.add_argument("--base", help="branch or commit to compare against (default: the default branch)")
    local.set_defaults(handler=lambda args: _guarded(lambda: local_context(here(), args.base)))
    evaluate = actions.add_parser("eval", help="score the reviewer on known-bug cases (uses your subscription)")
    evaluate.add_argument("--cases", default="docs/review-evals", help="directory of <case>/case.json files")
    evaluate.add_argument("--level", choices=["quick", "standard", "deep"], default="standard")
    evaluate.add_argument("--runs", type=int, default=3, help="sessions per case; judge repeated runs, not one")
    evaluate.add_argument("--json", help="also write the scores to this file")
    evaluate.set_defaults(handler=lambda args: _guarded(lambda: eval_command(args, here())))
    actions.add_parser("schema", help="print the JSON schema of the review result").set_defaults(
        handler=lambda _args: print(schema_json()) or 0
    )
