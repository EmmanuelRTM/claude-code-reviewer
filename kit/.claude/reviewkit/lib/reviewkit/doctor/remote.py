"""Checks that read repository settings from GitHub. Read-only: nothing is changed.

Several of these settings are visible to repository admins only. A check that
cannot see its setting says so and is skipped; it never guesses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional, Set, Tuple

from ..config import CONFIG_RELATIVE_PATH, Config
from ..github.client import GitHubError, GitHubPort
from .model import ADMIN_ONLY, FAIL, OK, SKIP, WARN, Check

SECRET_NAME = "CLAUDE_CODE_OAUTH_TOKEN"
# `claude setup-token` issues a one-year token; warn while there is still time to rotate it.
TOKEN_WARN_AFTER_DAYS = 330
REMOTE_IDS = ("gh-repo", "gh-secret", "gh-actions", "gh-protection")

_SETUP_TOKEN = (
    f"Run `claude setup-token` and save the value as the {SECRET_NAME} secret "
    "(Settings, Secrets and variables, Actions)."
)


def _age_days(timestamp: Any, now: datetime) -> Optional[int]:
    if not isinstance(timestamp, str):
        return None
    try:
        then = datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (now - then).days)


def _repository(gh: GitHubPort, base: str, name: str, cfg: Config) -> Tuple[List[Check], Optional[str]]:
    try:
        data = gh.get(base)
    except GitHubError as exc:
        fix = "Check the name (--repo owner/name) and that the token can read the repository."
        return [Check("gh-repo", FAIL, f"cannot read {name} (HTTP {exc.status})", fix)], None
    branch = data.get("default_branch") or cfg.default_branch
    checks: List[Check] = []
    if not data.get("has_issues"):
        summary = "Issues are disabled: issue intake, needs-human escalation and the metrics report need them"
        checks.append(Check("gh-repo", FAIL, summary, "Turn on Issues under Settings, General, Features."))
    if data.get("archived"):
        summary = "the repository is archived: no workflow runs"
        checks.append(Check("gh-repo", WARN, summary, "Unarchive it to use the kit."))
    if branch != cfg.default_branch:
        summary = f"the default branch is {branch}, but {CONFIG_RELATIVE_PATH} says {cfg.default_branch}"
        checks.append(Check("gh-repo", WARN, summary, f'Set "default_branch": "{branch}" in {CONFIG_RELATIVE_PATH}.'))
    return checks or [Check("gh-repo", OK, f"{name}: Issues are on, default branch {branch}")], branch


def _secret_age(where: str, updated_at: Any, now: datetime) -> Check:
    age = _age_days(updated_at, now)
    if age is not None and age >= TOKEN_WARN_AFTER_DAYS:
        summary = f"{SECRET_NAME} ({where}) was last set {age} days ago; a subscription token lasts one year"
        return Check("gh-secret", WARN, summary, _SETUP_TOKEN)
    when = f", set {age} days ago" if age is not None else ""
    return Check("gh-secret", OK, f"{SECRET_NAME} exists ({where}{when})")


def _secret(gh: GitHubPort, base: str, now: datetime) -> Check:
    try:
        return _secret_age("repository secret", gh.get(f"{base}/actions/secrets/{SECRET_NAME}").get("updated_at"), now)
    except GitHubError as exc:
        if exc.status != 404:
            return Check("gh-secret", SKIP, f"cannot read Actions secrets with this token ({ADMIN_ONLY})")
    try:
        shared = gh.get(f"{base}/actions/organization-secrets").get("secrets") or []
    except GitHubError as exc:
        if exc.status != 404:
            summary = f"no repository secret named {SECRET_NAME}; organization secrets cannot be read ({ADMIN_ONLY})"
            return Check("gh-secret", SKIP, summary)
        shared = []
    for secret in shared:
        if isinstance(secret, dict) and secret.get("name") == SECRET_NAME:
            return _secret_age("organization secret", secret.get("updated_at"), now)
    summary = f"no repository or organization secret named {SECRET_NAME}: the review cannot sign in to Claude"
    return Check("gh-secret", FAIL, summary, _SETUP_TOKEN + " An environment secret is not visible to this check.")


def _actions(gh: GitHubPort, base: str) -> Check:
    try:
        data = gh.get(f"{base}/actions/permissions/workflow")
    except GitHubError:
        return Check("gh-actions", SKIP, f"cannot read Actions settings with this token ({ADMIN_ONLY})")
    if data.get("can_approve_pull_request_reviews"):
        summary = "workflows may approve pull requests; the kit never approves, and approval should stay with people"
        fix = "Settings, Actions, General: clear 'Allow GitHub Actions to create and approve pull requests'."
        return Check("gh-actions", WARN, summary, fix)
    default = data.get("default_workflow_permissions") or "unknown"
    return Check("gh-actions", OK, f"workflows cannot approve pull requests (default token permission: {default})")


def _classic_protection(gh: GitHubPort, base: str, branch: str) -> Tuple[bool, Set[str], bool]:
    """(readable, required status contexts, code owner review required) from branch protection."""
    try:
        data = gh.get(f"{base}/branches/{branch}/protection")
    except GitHubError as exc:
        return exc.status == 404, set(), False
    status = data.get("required_status_checks") or {}
    contexts = {c for c in status.get("contexts") or [] if isinstance(c, str)}
    contexts |= {c.get("context") for c in status.get("checks") or [] if isinstance(c, dict) and c.get("context")}
    reviews = data.get("required_pull_request_reviews") or {}
    return True, contexts, bool(reviews.get("require_code_owner_reviews"))


def _ruleset_protection(gh: GitHubPort, base: str, branch: str) -> Tuple[bool, Set[str], bool]:
    """The same three facts from repository rulesets that apply to the branch."""
    try:
        rules = gh.get(f"{base}/rules/branches/{branch}")
    except GitHubError:
        return False, set(), False
    contexts: Set[str] = set()
    code_owner = False
    for rule in rules if isinstance(rules, list) else []:
        parameters = (rule.get("parameters") if isinstance(rule, dict) else None) or {}
        if rule.get("type") == "required_status_checks":
            required = parameters.get("required_status_checks") or []
            contexts |= {c.get("context") for c in required if isinstance(c, dict) and c.get("context")}
        elif rule.get("type") == "pull_request":
            code_owner = code_owner or bool(parameters.get("require_code_owner_review"))
    return True, contexts, code_owner


def _protection(gh: GitHubPort, base: str, branch: str, cfg: Config) -> Check:
    classic_readable, classic_contexts, classic_owner = _classic_protection(gh, base, branch)
    rules_readable, rule_contexts, rule_owner = _ruleset_protection(gh, base, branch)
    if not classic_readable and not rules_readable:
        return Check("gh-protection", SKIP, f"cannot read branch protection with this token ({ADMIN_ONLY})")
    wanted = [cfg.risk.status_context] + ([cfg.review.check_name] if cfg.review.publish == "gate" else [])
    required = classic_contexts | rule_contexts
    problems = [f"`{context}` is not a required status check" for context in wanted if context not in required]
    if not (classic_owner or rule_owner):
        problems.append("code owner review is not required")
    if problems:
        fix = (
            "Settings, Branches or Rules: require a pull request with code owner review "
            "and the status checks named here."
        )
        return Check("gh-protection", WARN, f"{branch}: " + "; ".join(problems), fix)
    return Check("gh-protection", OK, f"{branch} requires {', '.join(wanted)} and code owner review")


def github_checks(gh: GitHubPort, owner: str, repo: str, cfg: Config, *, now: Optional[datetime] = None) -> List[Check]:
    """Every remote check, in a fixed order. Never raises on an API error."""
    now = now or datetime.now(timezone.utc)
    base = f"/repos/{owner}/{repo}"
    checks, branch = _repository(gh, base, f"{owner}/{repo}", cfg)
    if branch is None:
        skipped = "skipped: the repository could not be read"
        return checks + [Check(check_id, SKIP, skipped) for check_id in REMOTE_IDS[1:]]
    return checks + [_secret(gh, base, now), _actions(gh, base), _protection(gh, base, branch, cfg)]
