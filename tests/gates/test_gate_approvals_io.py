"""Approval I/O: permission lookups, the commit status, and the shared-commit guard."""

from __future__ import annotations

import pytest

from fakes import FakeGitHub
from gates.gates_kit import HEAD, OLD, SHORT, calls, pull, raises
from reviewkit import approvals
from reviewkit.github.client import GitHubError

PERMISSION = r"/repos/o/r/collaborators/([^/]+)/permission"


def result(state: str = "success", approvers=("alice",), required: int = 1) -> approvals.ApprovalResult:
    return approvals.ApprovalResult(
        tier="high",
        required=required,
        approvers=tuple(approvers),
        state=state,
        description=f"risk:high: {len(approvers)} of {required} human approvals on {SHORT}",
    )


def test_fetch_permission_reads_the_permission_field(github: FakeGitHub) -> None:
    github.on("GET", PERMISSION, {"permission": "write", "role_name": "write", "user": {"login": "alice"}})
    permission_of = approvals.fetch_permission(github, "o", "r")
    assert permission_of("alice") == "write"
    assert calls(github) == [("GET", "/repos/o/r/collaborators/alice/permission")]


def test_fetch_permission_makes_no_call_until_asked(github: FakeGitHub) -> None:
    approvals.fetch_permission(github, "o", "r")
    assert github.calls == []


def test_fetch_permission_asks_github_once_per_login(github: FakeGitHub) -> None:
    table = {"alice": "admin", "bob": "read"}
    github.on("GET", PERMISSION, lambda match, params, body: {"permission": table[match.group(1)]})
    permission_of = approvals.fetch_permission(github, "o", "r")
    answers = [permission_of(login) for login in ("alice", "bob", "alice", "Alice", "bob")]
    assert answers == ["admin", "read", "admin", "admin", "read"]
    assert calls(github) == [
        ("GET", "/repos/o/r/collaborators/alice/permission"),
        ("GET", "/repos/o/r/collaborators/bob/permission"),
    ]


def test_someone_github_does_not_know_has_no_permission(github: FakeGitHub) -> None:
    github.on("GET", PERMISSION, raises(404))
    permission_of = approvals.fetch_permission(github, "o", "r")
    assert permission_of("stranger") == "none"
    assert permission_of("stranger") == "none"
    assert len(github.calls) == 1


def test_fetch_permission_raises_other_failures(github: FakeGitHub) -> None:
    github.on("GET", PERMISSION, raises(403))
    with pytest.raises(GitHubError):
        approvals.fetch_permission(github, "o", "r")("alice")


@pytest.mark.parametrize("payload", [{}, {"permission": None}, None, {"permission": 5}])
def test_an_answer_without_a_permission_is_none(github: FakeGitHub, payload) -> None:
    github.on("GET", PERMISSION, payload)
    assert approvals.fetch_permission(github, "o", "r")("alice") == "none"


def test_fetch_permission_url_encodes_the_login(github: FakeGitHub) -> None:
    github.on("GET", r"/repos/o/r/collaborators/.*", {"permission": "read"})
    approvals.fetch_permission(github, "o", "r")("odd/login?x")
    assert calls(github) == [("GET", "/repos/o/r/collaborators/odd%2Flogin%3Fx/permission")]


def test_post_status_sends_state_context_description_and_link(github: FakeGitHub) -> None:
    github.on("POST", r"/repos/o/r/statuses/[0-9a-f]+", {"id": 1})
    approvals.post_status(github, "o", "r", HEAD, result("pending", (), 2), "risk-approval", "https://example.test/run")
    assert github.calls == [
        {
            "method": "POST",
            "path": f"/repos/o/r/statuses/{HEAD}",
            "params": {},
            "body": {
                "state": "pending",
                "context": "risk-approval",
                "description": f"risk:high: 0 of 2 human approvals on {SHORT}",
                "target_url": "https://example.test/run",
            },
        }
    ]


def test_post_status_leaves_out_an_empty_link_and_clips_the_description(github: FakeGitHub) -> None:
    github.on("POST", r"/repos/o/r/statuses/[0-9a-f]+", {"id": 1})
    long = approvals.ApprovalResult("high", 1, (), "pending", "d" * 300)
    approvals.post_status(github, "o", "r", HEAD, long, "ctx", "")
    body = github.calls[0]["body"]
    assert set(body) == {"state", "context", "description"}
    assert len(body["description"]) == 140


def test_open_pulls_for_commit_keeps_only_open_pull_requests(github: FakeGitHub) -> None:
    listed = [pull(7), pull(8, state="closed"), pull(9), "junk"]
    github.on("GET", r"/repos/o/r/commits/[0-9a-f]+/pulls", listed)
    found = approvals.open_pulls_for_commit(github, "o", "r", HEAD)
    assert [item["number"] for item in found] == [7, 9]
    assert calls(github) == [("GET", f"/repos/o/r/commits/{HEAD}/pulls")]


@pytest.mark.parametrize(
    ("others", "guarded"),
    [
        ([], False),
        ([pull(7)], False),
        ([pull(7), pull(12)], True),
        ([pull(12)], True),
        # Closed, or merely containing the commit below a different head: not a sibling.
        ([pull(7), pull(12, state="closed")], False),
        ([pull(7), pull(12, head=OLD)], False),
        ([pull(7), pull(12, head=HEAD.upper())], True),
        # An open pull request whose head cannot be read is assumed to share it.
        ([pull(7), {"number": 12, "state": "open"}], True),
    ],
)
def test_sibling_guard(others, guarded: bool) -> None:
    before = result("success")
    after = approvals.apply_sibling_guard(before, others, 7, HEAD)
    if not guarded:
        assert after == before
        return
    assert after.state == "pending"
    assert after.approvers == before.approvers and after.required == before.required
    assert "another open pull request" in after.description
    assert "#12" in after.description and SHORT in after.description
    assert len(after.description) <= 140


def test_sibling_guard_also_explains_a_result_that_was_already_pending() -> None:
    after = approvals.apply_sibling_guard(result("pending", (), 2), [pull(7), pull(31), pull(12)], 7, HEAD)
    assert after.state == "pending"
    # The lowest-numbered sibling is named, so the message is stable between runs.
    assert "#12" in after.description and "#31" not in after.description


def test_sibling_guard_description_stays_within_the_limit_for_huge_numbers() -> None:
    wide = approvals.ApprovalResult("x" * 200, 1, (), "success", "ok")
    after = approvals.apply_sibling_guard(wide, [pull(10**12)], 7, HEAD)
    assert after.state == "pending" and len(after.description) <= 140
