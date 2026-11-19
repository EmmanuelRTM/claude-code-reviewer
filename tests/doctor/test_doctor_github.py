"""`doctor --github`: the repository settings the kit depends on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from doctor_kit import by_id
from reviewkit.config import Config, parse_config
from reviewkit.doctor.model import FAIL, OK, SKIP, WARN
from reviewkit.doctor.remote import github_checks
from reviewkit.github.client import GitHubError

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)
REPO = r"/repos/octo/app"


def iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def deny(status):
    def respond(match, params, body):
        raise GitHubError(status, "GET", match.group(0), "denied")

    return respond


@pytest.fixture
def healthy(github):
    github.on("GET", REPO, {"has_issues": True, "default_branch": "main", "archived": False})
    github.on("GET", REPO + r"/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN", {"name": "x", "updated_at": iso(30)})
    github.on(
        "GET", REPO + r"/actions/permissions/workflow",
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
    )  # fmt: skip
    github.on(
        "GET", REPO + r"/branches/main/protection",
        {
            "required_status_checks": {"contexts": ["risk-approval"], "checks": [{"context": "risk-approval"}]},
            "required_pull_request_reviews": {"require_code_owner_reviews": True},
        },
    )  # fmt: skip
    github.on("GET", REPO + r"/rules/branches/main", [])
    return github


def run(github, cfg=None):
    return by_id(github_checks(github, "octo", "app", cfg or Config(), now=NOW))


def test_a_well_configured_repository_passes(healthy):
    checks = run(healthy)
    assert {c.status for c in checks.values()} == {OK}
    assert set(checks) == {"gh-repo", "gh-secret", "gh-actions", "gh-protection"}


def test_only_reads_are_made(healthy):
    run(healthy)
    assert {call["method"] for call in healthy.calls} == {"GET"}


def test_disabled_issues_fail(healthy):
    healthy.on("GET", REPO, {"has_issues": False, "default_branch": "main"})
    check = run(healthy)["gh-repo"]
    assert check.status == FAIL and "Issues" in check.summary


def test_a_different_default_branch_is_reported(healthy):
    healthy.on("GET", REPO, {"has_issues": True, "default_branch": "trunk"})
    healthy.on("GET", REPO + r"/branches/trunk/protection", deny(404))
    healthy.on("GET", REPO + r"/rules/branches/trunk", [])
    check = run(healthy)["gh-repo"]
    assert check.status == WARN and "trunk" in check.summary and "default_branch" in check.fix


def test_an_unreachable_repository_fails_and_skips_the_rest(github):
    github.on("GET", REPO, deny(404))
    checks = run(github)
    assert checks["gh-repo"].status == FAIL
    assert {checks[name].status for name in ("gh-secret", "gh-actions", "gh-protection")} == {SKIP}


def test_a_missing_secret_fails_with_the_setup_command(healthy):
    healthy.on("GET", REPO + r"/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN", deny(404))
    healthy.on("GET", REPO + r"/actions/organization-secrets", {"total_count": 0, "secrets": []})
    check = run(healthy)["gh-secret"]
    assert check.status == FAIL and "claude setup-token" in check.fix


def test_an_organization_secret_counts(healthy):
    healthy.on("GET", REPO + r"/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN", deny(404))
    healthy.on(
        "GET", REPO + r"/actions/organization-secrets",
        {"total_count": 1, "secrets": [{"name": "CLAUDE_CODE_OAUTH_TOKEN", "updated_at": iso(10)}]},
    )  # fmt: skip
    assert run(healthy)["gh-secret"].status == OK


def test_a_token_without_access_to_secrets_skips_instead_of_guessing(healthy):
    healthy.on("GET", REPO + r"/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN", deny(403))
    check = run(healthy)["gh-secret"]
    assert check.status == SKIP and "admin" in check.summary


def test_a_secret_near_its_one_year_expiry_is_a_warning(healthy):
    healthy.on("GET", REPO + r"/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN", {"name": "x", "updated_at": iso(340)})
    check = run(healthy)["gh-secret"]
    assert check.status == WARN and "340" in check.summary and "claude setup-token" in check.fix


def test_workflows_allowed_to_approve_pull_requests_are_a_warning(healthy):
    healthy.on(
        "GET", REPO + r"/actions/permissions/workflow",
        {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True},
    )  # fmt: skip
    check = run(healthy)["gh-actions"]
    assert check.status == WARN and "approve" in check.summary


def test_actions_settings_need_admin_access(healthy):
    healthy.on("GET", REPO + r"/actions/permissions/workflow", deny(403))
    assert run(healthy)["gh-actions"].status == SKIP


def test_an_unprotected_default_branch_is_a_warning(healthy):
    healthy.on("GET", REPO + r"/branches/main/protection", deny(404))
    check = run(healthy)["gh-protection"]
    assert check.status == WARN and "risk-approval" in check.summary and "code owner" in check.summary


def test_protection_without_the_risk_check_is_a_warning(healthy):
    healthy.on(
        "GET", REPO + r"/branches/main/protection",
        {"required_status_checks": {"contexts": ["ci"]}, "required_pull_request_reviews": {"require_code_owner_reviews": True}},
    )  # fmt: skip
    check = run(healthy)["gh-protection"]
    assert check.status == WARN and "risk-approval" in check.summary and "code owner" not in check.summary


def test_rulesets_count_as_protection(healthy):
    healthy.on("GET", REPO + r"/branches/main/protection", deny(404))
    healthy.on(
        "GET", REPO + r"/rules/branches/main",
        [
            {"type": "pull_request", "parameters": {"require_code_owner_review": True}},
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "risk-approval"}]}},
        ],
    )  # fmt: skip
    assert run(healthy)["gh-protection"].status == OK


def test_gate_mode_also_wants_the_review_check_required(healthy):
    cfg, _ = parse_config({"version": 1, "review": {"publish": "gate"}})
    check = run(healthy, cfg)["gh-protection"]
    assert check.status == WARN and "Claude PR Review" in check.summary


def test_protection_that_cannot_be_read_is_skipped(healthy):
    healthy.on("GET", REPO + r"/branches/main/protection", deny(403))
    healthy.on("GET", REPO + r"/rules/branches/main", deny(403))
    assert run(healthy)["gh-protection"].status == SKIP
