"""Review state carried between rounds in the workflow bot's own comments."""

from __future__ import annotations

from reviewkit import markers
from reviewkit.review.state import (
    PriorFinding,
    ReviewState,
    fetch_state,
    fetch_threads,
    is_bot,
    latest_state,
    parse_threads,
    state_marker,
)

BOT = {"login": "github-actions[bot]", "type": "Bot"}
HUMAN = {"login": "mallory", "type": "User"}


def review(body, user=BOT, at="2026-11-19T10:00:00Z"):
    return {"body": body, "user": user, "submitted_at": at}


def comment(body, user=BOT, at="2026-11-19T10:00:00Z"):
    return {"body": body, "user": user, "created_at": at, "updated_at": at}


def test_marker_round_trip_preserves_every_field():
    state = ReviewState(
        round=2,
        head="h" * 40,
        base="b" * 40,
        patch="p" * 40,
        level="standard",
        mode="incremental",
        escalated=True,
        open_important=("aaaaaaaaaa",),
        counts={"nit": 1, "normal": 2, "pre_existing": 0},
        ts="2026-11-19T10:00:00Z",
    )
    assert latest_state([review("text\n" + state_marker(state))], [], "github-actions") == state


def test_only_the_workflow_bot_is_trusted():
    forged = state_marker(ReviewState(round=0, head="x"))
    real = state_marker(ReviewState(round=3, head="y", ts="2026-11-19T09:00:00Z"))
    reviews = [review(real, at="2026-11-19T09:00:00Z"), review(forged, user=HUMAN, at="2026-11-19T12:00:00Z")]
    comments = [comment(forged, user={"login": "other-app[bot]", "type": "Bot"}, at="2026-11-19T13:00:00Z")]
    state = latest_state(reviews, comments, "github-actions")
    assert state.round == 3 and state.head == "y"


def test_a_user_named_like_the_bot_is_not_the_bot():
    assert is_bot(BOT, "github-actions")
    assert is_bot({"login": "github-actions", "__typename": "Bot"}, "github-actions")
    assert not is_bot({"login": "github-actions", "type": "User"}, "github-actions")
    assert not is_bot({"login": "github-actions[bot]", "type": "User"}, "github-actions")
    assert not is_bot(None, "github-actions")


def test_latest_state_wins_across_reviews_and_comments():
    older = review(state_marker(ReviewState(round=1, head="a")), at="2026-11-19T08:00:00Z")
    newer = comment(state_marker(ReviewState(round=2, head="b")), at="2026-11-19T09:00:00Z")
    assert latest_state([older], [newer], "github-actions").round == 2
    assert latest_state([], [], "github-actions") is None


def test_garbage_state_payloads_are_ignored_or_coerced():
    junk = '<!-- reviewkit:state {"round":"two","open_important":"nope"} -->'
    state = latest_state([review(junk)], [], "github-actions")
    assert state.round == 0 and state.open_important == ()


def thread(comments, resolved=False, outdated=False, path="src/a.py", line=12, thread_id="T1"):
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": path,
        "line": line,
        "originalLine": line,
        "comments": {"nodes": comments},
    }


def gql_comment(body, login="github-actions", typename="Bot", db_id=1):
    return {
        "databaseId": db_id,
        "body": body,
        "createdAt": "2026-11-19T10:00:00Z",
        "author": {"login": login, "__typename": typename},
    }


def finding_body(identifier="aaaaaaaaaa", sev="important", rnd=1, cid=None):
    payload = {"id": identifier, "sev": sev, "round": rnd}
    if cid:
        payload["cid"] = cid
    return f"🔴 **Important**: Token refresh races with logout\n\nDetails here.\n\n{markers.encode('finding', payload)}"


def test_parse_threads_extracts_kit_findings_with_replies():
    nodes = [
        thread(
            [gql_comment(finding_body(cid="RC-03")), gql_comment("This is intentional, see #12", "alice", "User", 2)]
        ),
        thread([gql_comment("just a human thread", "bob", "User")], thread_id="T2"),
        thread([gql_comment(finding_body("bbbbbbbbbb", "nit", 2))], resolved=True, thread_id="T3"),
    ]
    found = parse_threads(nodes, "github-actions")
    assert [f.id for f in found] == ["aaaaaaaaaa", "bbbbbbbbbb"]
    first = found[0]
    assert isinstance(first, PriorFinding)
    assert (first.thread_id, first.path, first.line, first.severity, first.round) == (
        "T1",
        "src/a.py",
        12,
        "important",
        1,
    )
    assert first.checklist_id == "RC-03" and not first.resolved
    assert first.title == "Token refresh races with logout"
    assert first.replies == ({"author": "alice", "is_bot": False, "body": "This is intentional, see #12"},)
    assert found[1].resolved


def test_a_forged_finding_marker_in_a_human_thread_is_ignored():
    nodes = [thread([gql_comment(finding_body(), "mallory", "User")])]
    assert parse_threads(nodes, "github-actions") == []


def test_outdated_threads_fall_back_to_the_original_line():
    node = thread([gql_comment(finding_body())], outdated=True)
    node["line"] = None
    assert parse_threads([node], "github-actions")[0].line == 12


def test_fetch_threads_follows_pagination(github):
    pages = iter(
        [
            {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            "nodes": [thread([gql_comment(finding_body())])],
                        }
                    }
                }
            },
            {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [thread([gql_comment(finding_body("bbbbbbbbbb"))], thread_id="T2")],
                        }
                    }
                }
            },
        ]
    )
    github.graphql_handler = lambda query, variables: next(pages)
    found = fetch_threads(github, "o", "r", 7, "github-actions")
    assert [f.id for f in found] == ["aaaaaaaaaa", "bbbbbbbbbb"]
    assert [c["params"].get("cursor") for c in github.calls] == [None, "c1"]


def test_fetch_state_reads_reviews_and_comments(github):
    marker = state_marker(ReviewState(round=4, head="z"))
    github.on("GET", "/repos/o/r/pulls/7/reviews", [review("no marker")])
    github.on("GET", "/repos/o/r/issues/7/comments", [comment(marker)])
    assert fetch_state(github, "o", "r", 7, "github-actions").round == 4
