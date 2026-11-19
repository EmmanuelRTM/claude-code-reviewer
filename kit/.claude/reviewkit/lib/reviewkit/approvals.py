"""Tiered human approval: who has vouched for the exact commit at the head of a pull request.

The result becomes a commit status that branch protection can require. The
rules are deliberately mechanical:

* an approval covers one specific commit; a new push needs new approvals;
* only people who could push to the repository themselves count
  (permission `admin`, `maintain` or `write`); agents and bots never do;
* the latest signal from each person wins, so requesting changes after
  approving withdraws the approval;
* the pull request author cannot approve in GitHub's interface, so they vouch
  for commits an agent pushed for them with a `/approve <sha>` comment;
* a `/approve` comment counts only as it was posted: anyone with write access
  can edit another person's comment, so an edited one is ignored.

`evaluate_approvals` is pure. The functions below it talk to GitHub.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from .github.client import GitHubError, GitHubPort

COUNTING_PERMISSIONS = frozenset({"admin", "maintain", "write"})
# `/approve` must name the whole commit. A short prefix would be convenient but
# weak: a second commit that starts with the same 7 characters takes about
# 2^28 hashes to make, and an old `/approve` would then match it. The step
# summary offers the full sha to copy.
MIN_SHA_PREFIX = 40
MAX_DESCRIPTION = 140  # GitHub's limit for a commit status description
GATE_COMMANDS = ("/approve", "/recheck")

_APPROVE = re.compile(rf"/approve[ \t]+([0-9a-fA-F]{{{MIN_SHA_PREFIX},40}})[ \t]*\Z")
_BOT_SUFFIX = "[bot]"
_WITHDRAWALS = ("CHANGES_REQUESTED", "DISMISSED")
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)
_LATEST = datetime.max.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ApprovalResult:
    """Outcome of the gate for one head commit; `state` is `success` or `pending`."""

    tier: str
    required: int
    approvers: Tuple[str, ...]
    state: str
    description: str


@dataclass(frozen=True)
class _Signal:
    """One thing a person did that says "I approve this head" or "I do not"."""

    login: str
    when: datetime
    approves: bool


def parse_timestamp(text: Any) -> Optional[datetime]:
    """A GitHub timestamp (`2026-03-01T12:00:00Z`) as an aware datetime, or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_login(login: Any) -> str:
    """Lower-cased login without a `[bot]` suffix, for comparing accounts."""
    text = login.strip().lower() if isinstance(login, str) else ""
    return text[: -len(_BOT_SUFFIX)] if text.endswith(_BOT_SUFFIX) else text


def is_agent(login: str, user_type: Optional[str], agent_logins: Iterable[str]) -> bool:
    """Whether an account is a bot or one of the configured agents. Agents never approve."""
    if isinstance(user_type, str) and user_type.lower() == "bot":
        return True
    if isinstance(login, str) and login.strip().lower().endswith(_BOT_SUFFIX):
        return True
    return normalize_login(login) in {normalize_login(agent) for agent in agent_logins}


def first_line(body: Any) -> str:
    """The first line of a comment body, exactly as written (no trimming on the left)."""
    if not isinstance(body, str):
        return ""
    return body.split("\n", 1)[0].rstrip("\r")


def is_gate_command(body: Any) -> bool:
    """Whether a comment asks the gate to run: its first line starts with `/approve` or `/recheck`."""
    return first_line(body).startswith(GATE_COMMANDS)


def approved_sha_prefix(body: Any) -> Optional[str]:
    """The commit prefix named by a `/approve <sha>` first line, lower-cased, or None."""
    match = _APPROVE.match(first_line(body))
    return match.group(1).lower() if match else None


def _was_edited(comment: Mapping[str, Any]) -> bool:
    """Whether a comment was changed after it was posted.

    Anyone with write access, an agent's token included, can edit another
    person's comment while the comment keeps its original author. An edited
    `/approve` therefore proves nothing about what its author meant.
    """
    updated = comment.get("updated_at")
    return bool(updated) and updated != comment.get("created_at")


def _signals(
    reviews: Iterable[Mapping[str, Any]],
    comments: Iterable[Mapping[str, Any]],
    head: str,
    author: str,
    agent_logins: Sequence[str],
    author_can_approve: bool,
) -> List[_Signal]:
    found: List[_Signal] = []

    def add(item: Mapping[str, Any], approves: bool, timestamp: Any) -> None:
        user = item.get("user")
        if not isinstance(user, Mapping):
            return
        login = user.get("login")
        if not isinstance(login, str) or not login or is_agent(login, user.get("type"), agent_logins):
            return
        # A signal without a readable time must not win by accident: an undated
        # approval sorts first, an undated withdrawal sorts last.
        when = parse_timestamp(timestamp) or (_EARLIEST if approves else _LATEST)
        found.append(_Signal(login, when, approves))

    for review in reviews:
        if not isinstance(review, Mapping):
            continue
        state = str(review.get("state") or "").upper()
        if state == "APPROVED":
            reviewer = review.get("user")
            login = reviewer.get("login") if isinstance(reviewer, Mapping) else None
            if normalize_login(login) == normalize_login(author):
                continue  # GitHub forbids approving your own pull request; ignore it if it ever shows up
            if not head or str(review.get("commit_id") or "").lower() != head:
                continue  # about another commit: neither an approval nor a withdrawal of this one
            add(review, True, review.get("submitted_at"))
        elif state in _WITHDRAWALS:
            add(review, False, review.get("submitted_at"))
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        prefix = approved_sha_prefix(comment.get("body"))
        if not (prefix and head.startswith(prefix)) or _was_edited(comment):
            continue
        commenter = comment.get("user")
        login = commenter.get("login") if isinstance(commenter, Mapping) else None
        if not author_can_approve and normalize_login(login) == normalize_login(author):
            continue  # this repository wants every approval to come from someone else
        add(comment, True, comment.get("created_at"))
    return found


def _tag(tier: str) -> str:
    """`risk:<tier>`, bounded so the rest of a description always fits after it."""
    return f"risk:{tier}"[:40]


def _clip(text: str) -> str:
    return text if len(text) <= MAX_DESCRIPTION else text[: MAX_DESCRIPTION - 3] + "..."


def _describe(tier: str, required: int, count: int, head: str) -> str:
    short = head[:7] or "unknown"
    if required <= 0:
        return _clip(f"{_tag(tier)}: no human approval required for {short}")
    return _clip(f"{_tag(tier)}: {count} of {required} human approvals on {short}")


def evaluate_approvals(
    *,
    reviews: Iterable[Mapping[str, Any]],
    comments: Iterable[Mapping[str, Any]],
    head_sha: str,
    author: str,
    tier: str,
    required: int,
    agent_logins: Sequence[str],
    permission_of: Callable[[str], str],
    author_can_approve: bool = True,
) -> ApprovalResult:
    """Decide whether enough people approved `head_sha`.

    `reviews` and `comments` are the pull request's reviews and issue comments
    in REST shape. `permission_of` returns GitHub's permission string for a
    login; it is called at most once per login, and only for people whose
    latest signal approves this head.
    """
    head = head_sha.strip().lower() if isinstance(head_sha, str) else ""
    latest: Dict[str, _Signal] = {}
    signals = _signals(reviews, comments, head, author, agent_logins, author_can_approve)
    # On a tie the withdrawal sorts last and therefore wins.
    for signal in sorted(signals, key=lambda s: (s.when, not s.approves)):
        latest[signal.login.lower()] = signal
    candidates = sorted(signal.login for signal in latest.values() if signal.approves)
    approvers = [login for login in candidates if str(permission_of(login) or "").lower() in COUNTING_PERMISSIONS]
    return ApprovalResult(
        tier=tier,
        required=required,
        approvers=tuple(approvers),
        state="success" if len(approvers) >= required else "pending",
        description=_describe(tier, required, len(approvers), head),
    )


def apply_sibling_guard(
    result: ApprovalResult, pulls: Iterable[Mapping[str, Any]], number: int, head_sha: str
) -> ApprovalResult:
    """Hold the status back while another open pull request has the same head commit.

    A commit status belongs to a commit, not to a pull request. If two open
    pull requests share a head, a status computed for one would also unlock
    the other, whose tier and approvals may differ. `pulls` are the pull
    requests GitHub associates with the commit.
    """
    head = head_sha.lower()
    siblings: List[int] = []
    for pull in pulls:
        if not isinstance(pull, Mapping) or pull.get("state") != "open":
            continue
        other = pull.get("number")
        if not isinstance(other, int) or isinstance(other, bool) or other == number:
            continue
        other_head = (pull.get("head") or {}).get("sha")
        # A head we cannot read is assumed to be the shared one.
        if not isinstance(other_head, str) or other_head.lower() == head:
            siblings.append(other)
    if not siblings:
        return result
    description = _clip(
        f"{_tag(result.tier)}: another open pull request (#{min(siblings)}) shares commit {head[:7]}; "
        "close one, then comment /recheck"
    )
    return dataclasses.replace(result, state="pending", description=description)


def fetch_permission(gh: GitHubPort, owner: str, repo: str) -> Callable[[str], str]:
    """A `permission_of` backed by GitHub that asks at most once per login.

    Returns the `permission` field of the collaborator-permission endpoint
    (`admin`, `write`, `read`, `none`, ...). An account GitHub does not know
    has no permission.
    """
    cache: Dict[str, str] = {}

    def permission_of(login: str) -> str:
        key = login.lower()
        if key not in cache:
            try:
                data = gh.get(f"/repos/{owner}/{repo}/collaborators/{quote(login, safe='')}/permission")
            except GitHubError as err:
                if err.status != 404:
                    raise
                data = None
            value = data.get("permission") if isinstance(data, dict) else None
            cache[key] = value.lower() if isinstance(value, str) and value else "none"
        return cache[key]

    return permission_of


def post_status(
    gh: GitHubPort,
    owner: str,
    repo: str,
    sha: str,
    result: ApprovalResult,
    context: str,
    target_url: str = "",
) -> None:
    """Record the result as a commit status on `sha`."""
    body: Dict[str, Any] = {
        "state": result.state,
        "context": context,
        "description": result.description[:MAX_DESCRIPTION],
    }
    if target_url:
        body["target_url"] = target_url
    gh.post(f"/repos/{owner}/{repo}/statuses/{sha}", body)


def open_pulls_for_commit(gh: GitHubPort, owner: str, repo: str, sha: str) -> List[Dict[str, Any]]:
    """The open pull requests GitHub associates with a commit."""
    pulls = gh.get_all(f"/repos/{owner}/{repo}/commits/{sha}/pulls")
    return [pull for pull in pulls if isinstance(pull, dict) and pull.get("state") == "open"]
