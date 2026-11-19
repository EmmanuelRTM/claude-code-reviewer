"""Hardening rules every workflow in the kit must satisfy.

`check_workflow` returns human-readable violations, so the same function is
used on the real workflows and on deliberately broken ones (a rule that cannot
fail proves nothing).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Tuple

import yaml

PINNED = re.compile(r"[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}\Z")
TRUSTED_TRIGGERS = {"pull_request_target", "issue_comment", "issues", "workflow_dispatch", "schedule"}
UNSAFE_IN_SCRIPTS = ("${{ github.event", "${{github.event", "${{ github.head_ref", "${{ inputs.", "${{ steps.")
TOKEN_SECRET = "secrets.CLAUDE_CODE_OAUTH_TOKEN"
CLAUDE_ACTION = "anthropics/claude-code-action"


def load(text: str) -> Dict[str, Any]:
    data = yaml.safe_load(text)
    if True in data:  # PyYAML reads the bare key `on` as a boolean
        data["on"] = data.pop(True)
    return data


def triggers(workflow: Dict[str, Any]) -> set:
    value = workflow.get("on")
    if isinstance(value, str):
        return {value}
    return set(value or [])


def steps(workflow: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_name, step


def check_workflow(text: str, name: str) -> List[str]:
    workflow = load(text)
    problems: List[str] = []
    if workflow.get("permissions") != {}:
        problems.append(f"{name}: top-level permissions must be an empty map")
    for job_name, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job.get("permissions"), dict):
            problems.append(f"{name}/{job_name}: job must declare its permissions")
        if "timeout-minutes" not in job:
            problems.append(f"{name}/{job_name}: job must set timeout-minutes")
    for job_name, step in steps(workflow):
        uses = step.get("uses")
        if uses and not PINNED.match(uses):
            problems.append(f"{name}/{job_name}: action is not pinned to a commit: {uses}")
        script = step.get("run") or ""
        for needle in UNSAFE_IN_SCRIPTS:
            if needle in script:
                problems.append(f"{name}/{job_name}: script interpolates {needle.strip('${ ')} directly")
        if uses and uses.startswith(CLAUDE_ACTION):
            token = (step.get("with") or {}).get("claude_code_oauth_token")
            if token != "${{ " + TOKEN_SECRET + " }}":
                problems.append(f"{name}/{job_name}: Claude step must authenticate with the subscription token")
    if re.search(r"anthropic_api_key|ANTHROPIC_API_KEY", text):
        problems.append(f"{name}: must not reference an API key (subscription only)")
    if TOKEN_SECRET in text:
        untrusted = triggers(workflow) - TRUSTED_TRIGGERS
        if untrusted:
            problems.append(f"{name}: holds the subscription token but runs on {sorted(untrusted)}")
    if "pull_request_target" in triggers(workflow):
        for job_name, step in steps(workflow):
            if (step.get("uses") or "").startswith("actions/checkout") and "ref" in (step.get("with") or {}):
                problems.append(f"{name}/{job_name}: checkout under pull_request_target must not set ref")
            if re.search(r"gh pr checkout|git checkout", step.get("run") or ""):
                problems.append(f"{name}/{job_name}: must not check out pull request code")
    return problems
