"""The gates workflows and the task issue form: structure and safety rules.

PyYAML reads the key `on` as the boolean `True`, hence `workflow[True]`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
import yaml

from gates.gates_expressions import ExpressionError, evaluate, truthy
from reviewkit import issues

KIT_GITHUB = Path(__file__).resolve().parents[2] / "kit" / ".github"
WORKFLOW_DIR = KIT_GITHUB / "workflows"
WORKFLOWS = ("risk-approval.yml", "issue-intake.yml", "review-metrics.yml")
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
RK = "python3 -I .claude/reviewkit/rk.py "
EXPECTED_PERMISSIONS = {
    "risk-approval.yml": {"contents": "read", "pull-requests": "write", "issues": "write", "statuses": "write"},
    "issue-intake.yml": {"contents": "read", "issues": "write"},
    "review-metrics.yml": {"contents": "read", "issues": "write", "pull-requests": "read"},
}
EXPECTED_COMMAND = {
    "risk-approval.yml": RK + "risk evaluate",
    "issue-intake.yml": RK + "issue intake",
    "review-metrics.yml": RK + 'metrics report --days "$DAYS" --post-issue',
}


def text_of(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def load(name: str) -> Dict[Any, Any]:
    return yaml.safe_load(text_of(name))


def jobs(name: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    yield from load(name)["jobs"].items()


def steps(name: str) -> List[Dict[str, Any]]:
    return [step for _, job in jobs(name) for step in job["steps"]]


def triggers(name: str) -> Dict[str, Any]:
    return load(name)[True]


@pytest.mark.parametrize("name", WORKFLOWS)
def test_workflow_starts_with_a_comment_that_explains_it(name: str) -> None:
    lines = text_of(name).splitlines()
    header = []
    for line in lines:
        if not line.startswith("#"):
            break
        header.append(line)
    assert len(header) >= 4
    assert "trigger" in " ".join(header).lower()


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_permissions_by_default_and_least_privilege_per_job(name: str) -> None:
    workflow = load(name)
    assert workflow["permissions"] == {}
    for job_name, job in jobs(name):
        assert job["permissions"] == EXPECTED_PERMISSIONS[name], job_name


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_job_has_a_runner_and_a_timeout(name: str) -> None:
    for job_name, job in jobs(name):
        assert job["runs-on"] == "ubuntu-latest", job_name
        assert isinstance(job["timeout-minutes"], int) and 0 < job["timeout-minutes"] <= 30, job_name


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_action_is_pinned_to_a_full_commit_sha(name: str) -> None:
    used = [step["uses"] for step in steps(name) if "uses" in step]
    assert used == [CHECKOUT]
    for reference in used:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", reference)
    # The human-readable version sits in a trailing comment on the same line.
    assert re.search(rf"uses: {re.escape(CHECKOUT)} # v6\.1\.0$", text_of(name), flags=re.MULTILINE)


@pytest.mark.parametrize("name", WORKFLOWS)
def test_checkout_keeps_no_credentials_and_fetches_one_commit(name: str) -> None:
    (checkout,) = [step for step in steps(name) if "uses" in step]
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 1}


@pytest.mark.parametrize("name", WORKFLOWS)
def test_scripts_never_interpolate_expressions(name: str) -> None:
    scripts = [step["run"] for step in steps(name) if "run" in step]
    assert scripts
    for script in scripts:
        assert "${{ github.event" not in script
        assert "${{" not in script


@pytest.mark.parametrize("name", WORKFLOWS)
def test_the_library_runs_isolated_with_the_token_in_the_environment(name: str) -> None:
    (step,) = [step for step in steps(name) if "run" in step]
    assert step["run"].strip() == EXPECTED_COMMAND[name]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "secrets." not in text_of(name)


@pytest.mark.parametrize("name", WORKFLOWS)
def test_files_are_tidy(name: str) -> None:
    text = text_of(name)
    assert text.endswith("\n") and "\t" not in text
    assert all(line == line.rstrip() for line in text.splitlines())
    assert text.isascii()


def test_risk_approval_triggers_run_from_the_base_branch_only() -> None:
    on = triggers("risk-approval.yml")
    assert set(on) == {"pull_request_target", "issue_comment"}
    assert "pull_request" not in on and "pull_request_review" not in on
    assert on["pull_request_target"] == {
        "types": ["opened", "synchronize", "reopened", "ready_for_review", "labeled", "unlabeled"]
    }
    assert on["issue_comment"] == {"types": ["created"]}


def test_risk_approval_never_checks_out_pull_request_code() -> None:
    (checkout,) = [step for step in steps("risk-approval.yml") if "uses" in step]
    assert "ref" not in checkout.get("with", {})
    assert "repository" not in checkout.get("with", {})
    text = text_of("risk-approval.yml")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "head.sha" not in body and "head.ref" not in body and "head_ref" not in body


def test_risk_approval_header_explains_the_trigger_choice() -> None:
    header = text_of("risk-approval.yml").split("\nname:")[0]
    for phrase in ("pull_request_target", "issue_comment", "pull_request_review", "/approve", "/recheck", "base"):
        assert phrase in header, phrase


def test_risk_approval_job_runs_for_pull_requests_and_for_commands_only() -> None:
    ((_, job),) = list(jobs("risk-approval.yml"))
    condition = " ".join(job["if"].split())
    assert "github.event_name == 'pull_request_target'" in condition
    assert "github.event.issue.pull_request" in condition
    assert "startsWith(github.event.comment.body, '/approve')" in condition
    assert "startsWith(github.event.comment.body, '/recheck')" in condition


def test_risk_approval_concurrency_cannot_be_cancelled_by_unrelated_comments() -> None:
    concurrency = load("risk-approval.yml")["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    group = " ".join(concurrency["group"].split())
    assert group.startswith("${{") and group.endswith("}}") and "\n" not in concurrency["group"].strip()
    # One group per pull request, for both ways a pull request can trigger the run...
    assert "github.event.pull_request.number" in group and "github.event.issue.number" in group
    # ...only for comments that are commands on a pull request...
    assert "github.event.issue.pull_request" in group
    assert "startsWith(github.event.comment.body, '/approve')" in group
    assert "startsWith(github.event.comment.body, '/recheck')" in group
    # ...and a unique group for everything else, so nothing is cancelled.
    assert group.rstrip("} ").endswith("|| format('noop-{0}', github.run_id)")


def github_context(event_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    return {"github": {"event_name": event_name, "run_id": 9001, "event": event}}


def pr_comment(body: Any, number: int = 7) -> Dict[str, Any]:
    """An `issue_comment` payload on a pull request, with the other ids a real one carries."""
    issue = {"id": 880011, "number": number, "pull_request": {"url": "u"}, "user": {"id": 31}}
    return {"action": "created", "issue": issue, "comment": {"id": 990022, "body": body, "user": {"id": 32}}}


RISK_SCENARIOS = [
    # (event name, event payload, expected concurrency group, job runs)
    ("pull_request_target", {"number": 7, "pull_request": {"id": 770033, "number": 7}}, "risk-approval-7", True),
    ("pull_request_target", {"number": 12, "pull_request": {"id": 770044, "number": 12}}, "risk-approval-12", True),
    ("issue_comment", pr_comment("/approve a1b2c3d"), "risk-approval-7", True),
    ("issue_comment", pr_comment("/recheck"), "risk-approval-7", True),
    ("issue_comment", pr_comment("/recheck", number=12), "risk-approval-12", True),
    ("issue_comment", pr_comment("/approve a1b2c3d\nplus an explanation"), "risk-approval-7", True),
    # Comments that are not commands get a group of their own and do not run the job.
    ("issue_comment", pr_comment("Looks good to me"), "noop-9001", False),
    ("issue_comment", pr_comment("please /recheck"), "noop-9001", False),
    ("issue_comment", pr_comment(""), "noop-9001", False),
    ("issue_comment", pr_comment(None), "noop-9001", False),
    # A command on a plain issue is not for this gate.
    ("issue_comment", {"issue": {"number": 7}, "comment": {"body": "/approve a1b2c3d"}}, "noop-9001", False),
    ("issue_comment", {"issue": {"number": 7}, "comment": {"body": "/recheck"}}, "noop-9001", False),
]


@pytest.mark.parametrize(("event_name", "event", "group", "runs"), RISK_SCENARIOS)
def test_risk_approval_group_and_job_condition_agree(event_name: str, event, group: str, runs: bool) -> None:
    """The job runs exactly when the run holds its pull request's concurrency group."""
    workflow = load("risk-approval.yml")
    ((_, job),) = list(jobs("risk-approval.yml"))
    context = github_context(event_name, event)
    assert evaluate(workflow["concurrency"]["group"], context) == group
    assert truthy(evaluate(job["if"], context)) is runs


def test_two_unrelated_comments_never_share_a_group() -> None:
    group = load("risk-approval.yml")["concurrency"]["group"]
    first = github_context("issue_comment", pr_comment("thanks"))
    second = github_context("issue_comment", pr_comment("thanks"))
    second["github"]["run_id"] = 9002
    assert evaluate(group, first) != evaluate(group, second)


def test_issue_intake_group_is_per_issue() -> None:
    group = load("issue-intake.yml")["concurrency"]["group"]
    assert group == "issue-intake-${{ github.event.issue.number }}"
    assert (
        evaluate(
            "format('issue-intake-{0}', github.event.issue.number)", {"github": {"event": {"issue": {"number": 3}}}}
        )
        == "issue-intake-3"
    )


@pytest.mark.parametrize(
    ("inputs", "expected"), [({}, "30"), ({"days": ""}, "30"), ({"days": "7"}, "7"), ({"days": "90"}, "90")]
)
def test_review_metrics_days_falls_back_on_a_scheduled_run(inputs, expected: str) -> None:
    (step,) = [step for step in steps("review-metrics.yml") if "run" in step]
    assert evaluate(step["env"]["DAYS"], {"inputs": inputs}) == expected


def test_the_expression_evaluator_follows_githubs_rules() -> None:
    context = {"github": {"event_name": "push", "event": {"n": 0, "s": "", "obj": {}}}}
    assert evaluate("github.event_name == 'PUSH'", context) is True
    assert evaluate("github.event.missing.deeper", context) is None
    assert evaluate("github.event.obj && 'yes' || 'no'", context) == "yes"
    assert evaluate("github.event.n && 'yes' || 'no'", context) == "no"
    assert evaluate("github.event.s || format('{0}-{1}', 'a', 1)", context) == "a-1"
    assert evaluate("${{ !startsWith('/Approve x', '/approve') }}", context) is False
    assert evaluate("'it''s' != 'its'", context) is True
    for broken in ("a ==", "unknown('x')", "(a", "a b", "a # b"):
        with pytest.raises(ExpressionError):
            evaluate(broken, context)


def test_issue_intake_triggers() -> None:
    assert triggers("issue-intake.yml") == {"issues": {"types": ["opened", "edited"]}}


def test_issue_intake_runs_one_at_a_time_per_issue() -> None:
    concurrency = load("issue-intake.yml")["concurrency"]
    assert "github.event.issue.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_review_metrics_triggers() -> None:
    on = triggers("review-metrics.yml")
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "17 6 1 * *"}]
    days = on["workflow_dispatch"]["inputs"]["days"]
    assert days["default"] == "30" and days.get("required", False) is False
    assert days["description"]


def test_review_metrics_passes_the_input_through_the_environment() -> None:
    (step,) = [step for step in steps("review-metrics.yml") if "run" in step]
    assert "inputs.days" in step["env"]["DAYS"]
    assert "inputs." not in step["run"]
    assert re.search(r'cron: "17 6 1 \* \*"', text_of("review-metrics.yml"))


# -- the task issue form ------------------------------------------------------


def template() -> Dict[str, Any]:
    return yaml.safe_load((KIT_GITHUB / "ISSUE_TEMPLATE" / "task.yml").read_text(encoding="utf-8"))


def fields() -> Dict[str, Dict[str, Any]]:
    return {field["id"]: field for field in template()["body"]}


def test_template_is_a_named_issue_form() -> None:
    form = template()
    assert form["name"] == "Task"
    assert isinstance(form["description"], str) and form["description"].strip()


def test_template_fields_in_order_with_types_and_required_flags() -> None:
    summary = [
        (field["id"], field["type"], field["attributes"]["label"], field.get("validations", {}).get("required", False))
        for field in template()["body"]
    ]
    assert summary == [
        ("summary", "textarea", "Summary", True),
        ("acceptance-criteria", "textarea", "Acceptance criteria", True),
        ("risk", "dropdown", "Risk tier", True),
        ("verification", "textarea", "Verification plan", False),
        ("notes", "textarea", "Notes", False),
    ]


def test_template_labels_are_the_ones_the_parser_reads() -> None:
    found = fields()
    assert found["acceptance-criteria"]["attributes"]["label"].lower() == issues.FIELD_ACCEPTANCE
    assert found["risk"]["attributes"]["label"].lower() == issues.FIELD_RISK


def test_template_risk_options_and_descriptions() -> None:
    risk = fields()["risk"]["attributes"]
    assert risk["options"] == ["Low", "Medium", "High"]
    description = risk["description"]
    for word in (
        "Low",
        "Medium",
        "High",
        "docs",
        "feature",
        "bug-fix",
        "auth",
        "billing",
        "migrations",
        "CI workflows",
    ):
        assert word in description, word
    assert "default" not in risk


def test_template_acceptance_criteria_shows_checkboxes() -> None:
    attributes = fields()["acceptance-criteria"]["attributes"]
    assert attributes["placeholder"].lstrip().startswith("- [ ] ")
    assert attributes["placeholder"].count("- [ ] ") >= 2
    assert "done" in attributes["description"].lower()
    assert "end-to-end" in fields()["verification"]["attributes"]["description"]


@pytest.mark.parametrize("choice", ["Low", "Medium", "High"])
def test_a_body_rendered_from_the_template_round_trips_through_the_parser(choice: str) -> None:
    answers = {
        "summary": "Add an export button.",
        "acceptance-criteria": "- [ ] The button downloads a CSV",
        "risk": choice,
        "verification": "_No response_",
        "notes": "_No response_",
    }
    body = "\n\n".join(f"### {field['attributes']['label']}\n\n{answers[field['id']]}" for field in template()["body"])
    assert issues.intake(body) == issues.IntakeResult(risk=choice.lower(), has_acceptance_criteria=True)
    untouched = body.replace("- [ ] The button downloads a CSV", "_No response_")
    assert issues.intake(untouched) == issues.IntakeResult(risk=choice.lower(), has_acceptance_criteria=False)
