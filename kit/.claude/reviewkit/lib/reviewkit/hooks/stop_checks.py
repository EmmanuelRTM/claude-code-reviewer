"""Stop hook: keep Claude working until the project's checks pass.

Cheap by design: checks run only when this session has changed something that
has not passed yet (results are remembered per working-tree fingerprint). After
`checks.max_blocks` failed rounds the hook lets the turn end and tells the user
that a human is needed, instead of looping.
"""

from __future__ import annotations

from typing import Callable, Mapping

from ..checks import format_failure, run_checks
from ..config import ConfigError, hooks_disabled, load_config
from ..fingerprint import FingerprintError, is_clean, tree_fingerprint
from ..gitio import repo_root
from ..localstate import LocalState, remember
from . import hookio
from .hookio import ALLOW, BLOCK, HookResult

STATE_FILE = "checks.json"
_REMEMBERED_PASSES = 20
_NO_SHORTCUTS = "Fix the cause. Do not skip, weaken, delete or retry a test to get a pass."


def _load_state(state: LocalState) -> dict:
    data = state.read_json(STATE_FILE, {})
    if not isinstance(data.get("passed"), list):
        data["passed"] = []
    if not isinstance(data.get("sessions"), dict):
        data["sessions"] = {}
    return data


def _record_pass(data: dict, fingerprints) -> None:
    for fingerprint in fingerprints:
        if fingerprint and fingerprint not in data["passed"]:
            data["passed"].append(fingerprint)
    data["passed"] = data["passed"][-_REMEMBERED_PASSES:]


def evaluate(event: Mapping, env: Mapping[str, str], runner: Callable = run_checks) -> HookResult:
    if hooks_disabled(env) or event.get("permission_mode") == "plan":
        return ALLOW
    project = hookio.project_dir_for(event, env)
    if repo_root(project) is None:
        return ALLOW
    try:
        cfg = load_config(project).config
    except ConfigError as exc:
        return HookResult(0, None, f"reviewkit: checks on stop skipped ({exc})")
    if not any(getattr(cfg.commands, name, "") for name in cfg.checks.on_stop):
        return ALLOW
    try:
        fingerprint = tree_fingerprint(project)
        clean = is_clean(project)
    except FingerprintError as exc:
        return HookResult(0, None, f"reviewkit: checks on stop skipped ({exc})")

    state = LocalState(project)
    data = _load_state(state)
    session_id = str(event.get("session_id") or "unknown")
    session = data["sessions"].get(session_id)

    if session is None and clean:
        # First sight of a session with nothing uncommitted: remember where it started.
        remember(data["sessions"], session_id, {"baseline": fingerprint, "blocks": 0})
        state.write_json(STATE_FILE, data)
        return ALLOW
    session = session or {"baseline": "", "blocks": 0}
    if fingerprint in data["passed"] or fingerprint == session.get("baseline"):
        if session.get("blocks"):
            session["blocks"] = 0
            remember(data["sessions"], session_id, session)
            state.write_json(STATE_FILE, data)
        return ALLOW

    results = runner(cfg, project)
    failure = next((result for result in results if not result.ok), None)
    if failure is None:
        try:
            after = tree_fingerprint(project)  # checks may write artifacts; that tree passed too
        except FingerprintError:
            after = ""
        _record_pass(data, (fingerprint, after))
        session["blocks"] = 0
        remember(data["sessions"], session_id, session)
        state.write_json(STATE_FILE, data)
        return ALLOW

    blocks = int(session.get("blocks", 0)) + 1
    limit = cfg.checks.max_blocks
    escalate = blocks > limit
    session["blocks"] = 0 if escalate else blocks
    remember(data["sessions"], session_id, session)
    state.write_json(STATE_FILE, data)
    if escalate:
        rounds = f"after {limit} fix rounds" if limit else "and blocking is turned off"
        message = (
            f"reviewkit: the project's checks are still failing {rounds}, so the turn was allowed to end. "
            f"A human needs to decide what happens next. Last failure: {format_failure(failure)}"
        )
        return HookResult(0, hookio.system_message(message))
    return HookResult(
        BLOCK,
        None,
        f"Checks failed (attempt {blocks} of {limit} before this goes to a human).\n"
        f"{format_failure(failure)}\n{_NO_SHORTCUTS}",
    )


def main() -> int:
    return hookio.run(evaluate)
