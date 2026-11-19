"""What the reviewer remembers between rounds.

Nothing is stored outside the pull request: the state of the last review rides
in a hidden marker inside the workflow bot's own review or comment, and earlier
findings are the bot's own review threads. Markers are trusted only when the
comment was written by that bot account, so a pull request author cannot forge
them from a comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence

from .. import markers
from ..github.client import GitHubPort
from ..sanitize import clean_text, one_line
from .schema import SEVERITIES

STATE_KIND = "state"
FINDING_KIND = "finding"
_MAX_REPLIES = 10
_MAX_THREAD_PAGES = 20

_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first: 30) {
            nodes { databaseId body createdAt author { login __typename } }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class ReviewState:
    round: int = 0
    head: str = ""
    base: str = ""
    patch: str = ""
    level: str = ""
    mode: str = ""
    escalated: bool = False
    open_important: tuple = ()
    counts: Optional[dict] = None
    ts: str = ""


@dataclass(frozen=True)
class PriorFinding:
    id: str
    thread_id: str
    comment_id: Optional[int]
    path: str
    line: Optional[int]
    severity: str
    round: int
    checklist_id: str
    resolved: bool
    outdated: bool
    title: str
    excerpt: str
    replies: tuple = ()


def is_bot(user: Optional[Mapping[str, Any]], bot_login: str) -> bool:
    """True when `user` (REST or GraphQL shape) is the workflow's bot account."""
    if not isinstance(user, Mapping):
        return False
    kind = user.get("type") or user.get("__typename")
    login = str(user.get("login") or "")
    if login.endswith("[bot]"):
        login = login[: -len("[bot]")]
    return kind == "Bot" and login == bot_login


def state_marker(state: ReviewState) -> str:
    payload = {
        "v": 1,
        "round": state.round,
        "head": state.head,
        "base": state.base,
        "patch": state.patch,
        "level": state.level,
        "mode": state.mode,
        "escalated": state.escalated,
        "open_important": list(state.open_important),
        "counts": state.counts or {},
        "ts": state.ts,
    }
    return markers.encode(STATE_KIND, payload)


def _state_from(payload: Mapping[str, Any]) -> ReviewState:
    def text(key: str) -> str:
        return payload[key] if isinstance(payload.get(key), str) else ""

    number = payload.get("round")
    important = payload.get("open_important")
    counts = payload.get("counts")
    return ReviewState(
        round=number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else 0,
        head=text("head"),
        base=text("base"),
        patch=text("patch"),
        level=text("level"),
        mode=text("mode"),
        escalated=payload.get("escalated") is True,
        open_important=tuple(i for i in important if isinstance(i, str)) if isinstance(important, list) else (),
        counts=counts if isinstance(counts, dict) and counts else None,
        ts=text("ts"),
    )


def latest_state(reviews: Sequence[Mapping], comments: Sequence[Mapping], bot_login: str) -> Optional[ReviewState]:
    """Most recent state marker among the bot's own reviews and comments."""
    candidates = []
    for item in list(reviews) + list(comments):
        if not is_bot(item.get("user"), bot_login):
            continue
        payload = markers.decode_last(STATE_KIND, item.get("body") or "")
        if payload is not None:
            when = item.get("submitted_at") or item.get("updated_at") or item.get("created_at") or ""
            candidates.append((str(when), len(candidates), payload))
    if not candidates:
        return None
    return _state_from(max(candidates)[2])


def fetch_state(gh: GitHubPort, owner: str, repo: str, number: int, bot_login: str) -> Optional[ReviewState]:
    reviews = gh.get_all(f"/repos/{owner}/{repo}/pulls/{number}/reviews")
    comments = gh.get_all(f"/repos/{owner}/{repo}/issues/{number}/comments")
    return latest_state(reviews, comments, bot_login)


def _title_of(body: str) -> str:
    first = next((line for line in markers.strip(body).split("\n") if line.strip()), "")
    for separator in ("**: ", "** · ", "**— "):
        if separator in first:
            first = first.split(separator, 1)[1]
            break
    return one_line(first, 200)


def parse_threads(nodes: Sequence[Mapping], bot_login: str) -> List[PriorFinding]:
    """Review threads that this kit opened, with any replies people left on them."""
    found: List[PriorFinding] = []
    for node in nodes:
        comments = ((node.get("comments") or {}).get("nodes")) or []
        if not comments or not is_bot(comments[0].get("author"), bot_login):
            continue
        body = comments[0].get("body") or ""
        payload = markers.decode_last(FINDING_KIND, body)
        if not payload or not isinstance(payload.get("id"), str) or payload.get("sev") not in SEVERITIES:
            continue
        line = node.get("line") if isinstance(node.get("line"), int) else node.get("originalLine")
        replies = tuple(
            {
                "author": one_line((reply.get("author") or {}).get("login"), 60),
                "is_bot": (reply.get("author") or {}).get("__typename") == "Bot",
                "body": clean_text(markers.strip(reply.get("body") or ""), 500),
            }
            for reply in comments[1 : 1 + _MAX_REPLIES]
        )
        found.append(
            PriorFinding(
                id=payload["id"],
                thread_id=str(node.get("id") or ""),
                comment_id=comments[0].get("databaseId") if isinstance(comments[0].get("databaseId"), int) else None,
                path=str(node.get("path") or ""),
                line=line if isinstance(line, int) else None,
                severity=payload["sev"],
                round=payload["round"] if isinstance(payload.get("round"), int) else 0,
                checklist_id=payload["cid"] if isinstance(payload.get("cid"), str) else "",
                resolved=node.get("isResolved") is True,
                outdated=node.get("isOutdated") is True,
                title=_title_of(body),
                excerpt=clean_text(markers.strip(body), 700),
                replies=replies,
            )
        )
    return found


def fetch_threads(gh: GitHubPort, owner: str, repo: str, number: int, bot_login: str) -> List[PriorFinding]:
    nodes: list = []
    cursor = None
    for _ in range(_MAX_THREAD_PAGES):
        data = gh.graphql(_THREADS_QUERY, {"owner": owner, "name": repo, "number": number, "cursor": cursor})
        threads = (((data.get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")) or {}
        nodes.extend(threads.get("nodes") or [])
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return parse_threads(nodes, bot_login)
