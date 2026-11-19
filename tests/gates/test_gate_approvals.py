"""Who counts as having approved the head commit of a pull request (pure rules)."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import pytest

from gates.gates_kit import EARLY, HEAD, LATE, NOON, OLD, SHORT, comment, review
from reviewkit import approvals

AGENTS = ("claude", "github-actions", "dependabot", "copilot")


class Permissions:
    """A `permission_of` that answers from a table and records every lookup."""

    def __init__(self, table: Dict[str, str], default: str = "write") -> None:
        self.table = table
        self.default = default
        self.lookups: List[str] = []

    def __call__(self, login: str) -> str:
        self.lookups.append(login)
        return self.table.get(login, self.default)


def evaluate(
    reviews: Sequence[Dict[str, Any]] = (),
    comments: Sequence[Dict[str, Any]] = (),
    *,
    required: int = 1,
    tier: str = "medium",
    author: str = "dev",
    head: str = HEAD,
    permissions: Any = None,
    author_can_approve: bool = True,
) -> approvals.ApprovalResult:
    return approvals.evaluate_approvals(
        reviews=reviews,
        comments=comments,
        head_sha=head,
        author=author,
        tier=tier,
        required=required,
        agent_logins=AGENTS,
        permission_of=permissions or Permissions({}),
        author_can_approve=author_can_approve,
    )


def test_no_signals_is_pending() -> None:
    result = evaluate()
    assert result == approvals.ApprovalResult(
        tier="medium",
        required=1,
        approvers=(),
        state="pending",
        description=f"risk:medium: 0 of 1 human approvals on {SHORT}",
    )


def test_an_approving_review_of_the_head_commit_counts() -> None:
    result = evaluate([review("alice")])
    assert result.approvers == ("alice",)
    assert result.state == "success"
    assert result.description == f"risk:medium: 1 of 1 human approvals on {SHORT}"


def test_an_approval_of_an_older_commit_does_not_cover_this_head() -> None:
    result = evaluate([review("alice", commit=OLD)])
    assert result.approvers == () and result.state == "pending"


@pytest.mark.parametrize(
    ("login", "kind"),
    [
        ("renovate[bot]", "Bot"),
        ("some-app", "Bot"),
        ("claude", "User"),
        ("Claude", "User"),
        ("claude[bot]", "User"),
        ("github-actions[bot]", "User"),
        ("Dependabot[bot]", "User"),
        ("copilot", "User"),
        ("unlisted[bot]", "User"),
    ],
)
def test_agents_and_bots_never_count(login: str, kind: str) -> None:
    permissions = Permissions({}, default="admin")
    by_review = evaluate([review(login, kind=kind)], permissions=permissions)
    by_comment = evaluate(comments=[comment(login, f"/approve {HEAD}", kind=kind)], permissions=permissions)
    assert by_review.approvers == () and by_comment.approvers == ()
    assert permissions.lookups == []


@pytest.mark.parametrize(
    ("permission", "counts"),
    [
        ("admin", True),
        ("maintain", True),
        ("write", True),
        ("WRITE", True),
        ("triage", False),
        ("read", False),
        ("none", False),
        ("", False),
    ],
)
def test_only_people_who_can_push_count(permission: str, counts: bool) -> None:
    result = evaluate([review("alice")], permissions=Permissions({"alice": permission}))
    assert (result.approvers == ("alice",)) is counts


def test_permissions_are_looked_up_lazily_and_once_per_login() -> None:
    permissions = Permissions({"mallory": "read"})
    reviews = [
        review("alice", at=EARLY),
        review("alice", at=LATE),
        review("Alice", state="COMMENTED", at=LATE),
        review("bob", state="CHANGES_REQUESTED"),
        review("carol", state="COMMENTED"),
        review("dave", commit=OLD),
        review("mallory"),
    ]
    comments = [
        comment("alice", f"/approve {HEAD}"),
        comment("erin", "looks good to me"),
        comment("frank", f"/approve {OLD[:7]}"),
    ]
    result = evaluate(reviews, comments, required=2, permissions=permissions)
    # Only people whose latest signal approves this head are looked up, each once.
    assert sorted(permissions.lookups) == ["alice", "mallory"]
    assert result.approvers == ("alice",)
    assert result.state == "pending"


@pytest.mark.parametrize(
    ("reviews", "comments", "approved"),
    [
        # Latest signal wins, whatever its kind.
        ([review("alice", at=EARLY), review("alice", "CHANGES_REQUESTED", at=LATE)], [], False),
        ([review("alice", "CHANGES_REQUESTED", at=EARLY), review("alice", at=LATE)], [], True),
        ([review("alice", at=EARLY), review("alice", "DISMISSED", at=LATE)], [], False),
        ([review("alice", "DISMISSED", at=NOON)], [], False),
        ([review("alice", "DISMISSED", at=EARLY), review("alice", at=LATE)], [], True),
        ([review("alice", "CHANGES_REQUESTED", at=LATE)], [comment("alice", f"/approve {HEAD}", at=EARLY)], False),
        ([review("alice", "CHANGES_REQUESTED", at=EARLY)], [comment("alice", f"/approve {HEAD}", at=LATE)], True),
        ([review("alice", "DISMISSED", at=EARLY)], [comment("alice", f"/approve {HEAD}", at=LATE)], True),
        # The order in which GitHub returns them does not matter.
        ([review("alice", "CHANGES_REQUESTED", at=LATE), review("alice", at=EARLY)], [], False),
        ([review("alice", at=LATE), review("alice", "CHANGES_REQUESTED", at=EARLY)], [], True),
        # Reviews that carry no verdict are not signals.
        ([review("alice", at=EARLY), review("alice", "COMMENTED", at=LATE)], [], True),
        ([review("alice", at=EARLY), {**review("alice", "PENDING"), "submitted_at": None}], [], True),
        ([review("alice", "COMMENTED")], [], False),
        # An approval of another commit says nothing about this one, in either direction.
        ([review("alice", at=EARLY), review("alice", commit=OLD, at=LATE)], [], True),
        ([review("alice", "CHANGES_REQUESTED", at=EARLY), review("alice", commit=OLD, at=LATE)], [], False),
        # Requested changes withdraw an approval even when made on another commit.
        ([review("alice", at=EARLY), review("alice", "CHANGES_REQUESTED", commit=OLD, at=LATE)], [], False),
        # Same second: the withdrawal wins.
        ([review("alice", at=NOON), review("alice", "CHANGES_REQUESTED", at=NOON)], [], False),
        ([review("alice", "CHANGES_REQUESTED", at=NOON)], [comment("alice", f"/approve {HEAD}", at=NOON)], False),
        # One person's withdrawal does not touch another person's approval.
        ([review("bob", "CHANGES_REQUESTED", at=LATE), review("alice", at=EARLY)], [], True),
    ],
)
def test_latest_signal_per_person_wins(reviews, comments, approved: bool) -> None:
    assert (evaluate(reviews, comments).approvers == ("alice",)) is approved


def test_a_signal_without_a_timestamp_never_wins_by_accident() -> None:
    undated_approval = {**review("alice"), "submitted_at": None}
    assert evaluate([undated_approval, review("alice", "CHANGES_REQUESTED", at=EARLY)]).approvers == ()
    undated_withdrawal = {**review("alice", "CHANGES_REQUESTED"), "submitted_at": "not a time"}
    assert evaluate([undated_withdrawal, review("alice", at=LATE)]).approvers == ()


@pytest.mark.parametrize(
    ("body", "approved"),
    [
        (f"/approve {HEAD}", True),
        (f"/approve {HEAD.upper()}", True),
        (f"/approve   {HEAD}  ", True),
        (f"/approve\t{HEAD}", True),
        (f"/approve {HEAD}\nI read every line of it.", True),
        (f"/approve {HEAD}\r\nWindows line ending", True),
        # A prefix is not enough: a second commit sharing a short prefix is cheap to forge,
        # and an old approval would then match it. Only the full sha names one commit.
        (f"/approve {HEAD[:7]}", False),
        (f"/approve {HEAD[:12]}", False),
        (f"/approve {HEAD[:39]}", False),
        # Too long, not hex.
        (f"/approve {HEAD}0", False),
        ("/approve zzzzzzz", False),
        ("/approve", False),
        ("/approve ", False),
        # A sha that is not the head: stale or simply wrong.
        (f"/approve {OLD[:7]}", False),
        (f"/approve {OLD}", False),
        (f"/approve {HEAD[1:9]}", False),
        # Only the first line, and only the exact command.
        (f"Looks good.\n/approve {HEAD}", False),
        (f"\n/approve {HEAD}", False),
        (f" /approve {HEAD}", False),
        (f"/approve {HEAD} please merge", False),
        (f"/approved {HEAD}", False),
        (f"/approve{HEAD}", False),
        (f"> /approve {HEAD}", False),
        (f"/APPROVE {HEAD}", False),
        (f"/recheck {HEAD}", False),
        ("", False),
    ],
)
def test_approve_comment_must_name_the_head_commit(body: str, approved: bool) -> None:
    assert (evaluate(comments=[comment("alice", body)]).approvers == ("alice",)) is approved


def test_a_comment_without_a_body_is_ignored() -> None:
    assert evaluate(comments=[{**comment("alice", ""), "body": None}]).approvers == ()


def test_the_author_vouches_with_an_approve_comment_and_counts_once() -> None:
    comments = [comment("dev", f"/approve {HEAD}", at=EARLY), comment("dev", f"/approve {HEAD}", at=LATE)]
    result = evaluate(comments=comments, author="dev", required=2)
    assert result.approvers == ("dev",)
    assert result.description == f"risk:medium: 1 of 2 human approvals on {SHORT}"
    assert evaluate(comments=comments, author="dev", required=1).state == "success"


def test_a_native_approval_by_the_author_is_ignored() -> None:
    assert evaluate([review("dev")], author="dev").approvers == ()
    assert evaluate([review("Dev")], author="dev").approvers == ()
    # Ignored means ignored: it does not displace the author's own earlier /approve either.
    comments = [comment("dev", f"/approve {HEAD}", at=EARLY)]
    assert evaluate([review("dev", at=LATE)], comments, author="dev").approvers == ("dev",)


def test_an_edited_approve_comment_is_ignored() -> None:
    """Anyone with write access can edit another person's comment, so an edited one proves nothing."""
    edited = comment("alice", f"/approve {HEAD}", at=EARLY, updated_at=LATE)
    assert evaluate(comments=[edited]).approvers == ()
    # Input in the minimal documented shape (no `updated_at`) still works.
    minimal = {"user": {"login": "alice", "type": "User"}, "body": f"/approve {HEAD}", "created_at": NOON}
    assert evaluate(comments=[minimal]).approvers == ("alice",)


def test_signals_without_a_user_are_ignored() -> None:
    ghost_review = {**review("ghost"), "user": None}
    ghost_comment = {**comment("ghost", f"/approve {HEAD}"), "user": None}
    assert evaluate([ghost_review], [ghost_comment]).approvers == ()


def test_malformed_entries_are_skipped_not_fatal() -> None:
    reviews = ["junk", None, {**review("x"), "user": "x"}, {**review("y"), "user": {"login": 7}}, review("alice")]
    comments = [42, {**comment("z", f"/approve {HEAD}"), "user": ["z"]}, {"body": f"/approve {HEAD}"}]
    assert evaluate(reviews, comments).approvers == ("alice",)


def test_one_person_is_one_approval_however_the_login_is_cased() -> None:
    result = evaluate([review("Alice", at=EARLY)], [comment("alice", f"/approve {HEAD}", at=LATE)], required=2)
    assert len(result.approvers) == 1 and result.state == "pending"


def test_a_missing_head_sha_approves_nothing() -> None:
    blank = {**review("alice"), "commit_id": ""}
    result = evaluate([blank, review("bob", commit=None)], [comment("carol", "/approve abcdef1")], head="")
    assert result.approvers == () and result.state == "pending"


@pytest.mark.parametrize(
    ("required", "approving", "state"),
    [
        (0, [], "success"),
        (0, ["alice"], "success"),
        (1, [], "pending"),
        (1, ["alice"], "success"),
        (2, ["alice"], "pending"),
        (2, ["alice", "bob"], "success"),
        (2, ["alice", "bob", "carol"], "success"),
    ],
)
def test_state_is_success_once_enough_people_approved(required: int, approving, state: str) -> None:
    result = evaluate([review(login) for login in approving], required=required, tier="high")
    assert result.state == state
    assert result.required == required and result.tier == "high"
    assert len(result.approvers) == len(approving)


def test_approvers_are_sorted() -> None:
    result = evaluate([review("zoe"), review("bob"), review("alice")], required=3)
    assert result.approvers == ("alice", "bob", "zoe")


@pytest.mark.parametrize(
    ("tier", "required", "approving", "expected"),
    [
        ("high", 2, ["alice"], f"risk:high: 1 of 2 human approvals on {SHORT}"),
        ("high", 2, ["alice", "bob"], f"risk:high: 2 of 2 human approvals on {SHORT}"),
        ("medium", 1, [], f"risk:medium: 0 of 1 human approvals on {SHORT}"),
        ("low", 0, [], f"risk:low: no human approval required for {SHORT}"),
        ("low", 0, ["alice"], f"risk:low: no human approval required for {SHORT}"),
    ],
)
def test_description(tier: str, required: int, approving, expected: str) -> None:
    result = evaluate([review(login) for login in approving], required=required, tier=tier)
    assert result.description == expected


@pytest.mark.parametrize(("tier", "required"), [("x" * 500, 2), ("high", 10**200), ("x" * 500, 10**200)])
def test_description_never_exceeds_what_a_commit_status_accepts(tier: str, required: int) -> None:
    result = evaluate([review("alice")], tier=tier, required=required)
    assert len(result.description) <= 140
    assert result.description.startswith("risk:")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (f"/approve {HEAD}", True),
        ("/approve", True),
        ("/approve not-a-sha", True),
        ("/recheck", True),
        ("/recheck please\nmore text", True),
        ("/recheck\r\n", True),
        ("Thanks!\n/approve abc1234", False),
        (" /approve abc1234", False),
        ("please /recheck", False),
        ("/retest", False),
        ("", False),
        (None, False),
    ],
)
def test_is_gate_command_looks_at_the_start_of_the_first_line(body, expected: bool) -> None:
    assert approvals.is_gate_command(body) is expected


@pytest.mark.parametrize(
    ("login", "kind", "expected"),
    [
        ("alice", "User", False),
        ("alice", None, False),
        ("alice", "Bot", True),
        ("alice", "bot", True),
        ("claude", "User", True),
        ("CLAUDE[bot]", "User", True),
        ("claudette", "User", False),
        ("anything[bot]", None, True),
    ],
)
def test_is_agent(login: str, kind, expected: bool) -> None:
    assert approvals.is_agent(login, kind, AGENTS) is expected


def test_configured_agent_logins_are_normalized_too() -> None:
    assert approvals.is_agent("release-bot", "User", ("Release-Bot[bot]",)) is True


def test_the_author_can_be_barred_from_approving_their_own_pull_request() -> None:
    comments = [comment("dev", f"/approve {HEAD}"), comment("alice", f"/approve {HEAD}")]
    allowed = evaluate(comments=comments, author="dev", required=2)
    barred = evaluate(comments=comments, author="dev", required=2, author_can_approve=False)
    assert allowed.approvers == ("alice", "dev")
    assert barred.approvers == ("alice",)
    # Another person's approval is unaffected, and so is the comparison of logins by case.
    assert evaluate(comments=[comment("Dev", f"/approve {HEAD}")], author="dev", author_can_approve=False).approvers == ()
