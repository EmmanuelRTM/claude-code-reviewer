"""PreToolUse gate on `git commit`: was this exact tree verified?

Modes: `off`, `observe` (allow, but log what enforce would block and tell
Claude), `enforce` (block with a reason). A new gate starts in observe so its
false positives are known before it can stop anyone. In enforce mode the gate
lets a commit through after `gate.max_blocks` blocked attempts in one session,
so a session can never get stuck; that commit is logged as unverified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..config import ConfigError, GateConfig, effective_gate_mode, hooks_disabled, load_config
from ..fingerprint import FingerprintError, changed_paths, tree_fingerprint
from ..gitio import repo_root
from ..localstate import LocalState, remember, utc_now
from ..paths import match_any
from . import hookio
from .gitcmd import TrailerInfo, is_git_commit, read_trailer
from .hookio import ALLOW, BLOCK, HookResult

MARKER_FILE = "verified.json"
LOG_FILE = "gate.log"
SESSIONS_FILE = "gate-sessions.json"

_HOW_TO_VERIFY = (
    "Run the verify skill after your last edit (it records the verification), run /code-review on the "
    "final diff, then commit again."
)
_OPT_OUT_HINT = (
    "If verification does not apply to this change, end the commit message with `Verified-By: none (<reason>)`."
)
MESSAGES = {
    "unverified": f"The current changes have not been verified. {_HOW_TO_VERIFY} {_OPT_OUT_HINT}",
    "opt_out_without_reason": (
        "`Verified-By: none` needs a reason in parentheses, for example `Verified-By: none (docs only)`. "
        f"Otherwise: {_HOW_TO_VERIFY}"
    ),
    "missing_trailer": (
        "The commit message has no `Verified-By:` trailer. End it with what you ran, for example "
        f"`Verified-By: verify, code-review`. {_OPT_OUT_HINT}"
    ),
}


@dataclass(frozen=True)
class GateFacts:
    trailer: TrailerInfo
    verified: bool
    exempt: bool
    clean: bool
    blocks: int


@dataclass(frozen=True)
class Decision:
    allow: bool
    event: str
    problem: str = ""


def decide(mode: str, gate: GateConfig, facts: GateFacts) -> Decision:
    """Pure gate decision. `mode` is `observe` or `enforce`."""
    if facts.trailer.opted_out:
        return Decision(True, "opt_out")
    if facts.exempt:
        return Decision(True, "exempt")
    if facts.verified:
        if not (gate.require_trailer and facts.trailer.message_visible and not facts.trailer.present):
            return Decision(True, "verified")
        problem = "missing_trailer"
    elif facts.clean:
        return Decision(True, "no_changes")
    else:
        declared_none = facts.trailer.present and facts.trailer.value.lower().startswith("none")
        problem = "opt_out_without_reason" if declared_none else "unverified"
    if mode == "observe":
        return Decision(True, f"would_block:{problem}", problem)
    if facts.blocks >= gate.max_blocks:
        return Decision(True, "allowed_after_max_blocks", problem)
    return Decision(False, f"blocked:{problem}", problem)


def _gather(command: str, project, gate: GateConfig, state: LocalState, session: str) -> tuple:
    fingerprint = tree_fingerprint(project)
    paths = changed_paths(project)
    marker = state.read_json(MARKER_FILE, {})
    facts = GateFacts(
        trailer=read_trailer(command, project),
        verified=bool(fingerprint) and marker.get("fingerprint") == fingerprint,
        exempt=bool(paths) and all(match_any(gate.exempt_paths, path) for path in paths),
        clean=not paths,
        blocks=int(state.read_json(SESSIONS_FILE, {}).get(session, 0)),
    )
    return facts, fingerprint


def evaluate(event: Mapping, env: Mapping[str, str]) -> HookResult:
    if event.get("tool_name") != "Bash" or hooks_disabled(env):
        return ALLOW
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not is_git_commit(command):
        return ALLOW
    project = hookio.project_dir_for(event, env)
    if repo_root(project) is None:
        return ALLOW
    try:
        cfg = load_config(project).config
    except ConfigError as exc:
        return HookResult(0, None, f"reviewkit: commit gate skipped ({exc})")
    mode = effective_gate_mode(cfg, env)
    if mode == "off":
        return ALLOW
    state = LocalState(project)
    session = str(event.get("session_id") or "unknown")
    try:
        facts, fingerprint = _gather(command, project, cfg.gate, state, session)
    except FingerprintError as exc:
        return HookResult(0, None, f"reviewkit: commit gate skipped ({exc})")
    decision = decide(mode, cfg.gate, facts)
    state.append_line(
        LOG_FILE,
        {"ts": utc_now(), "session": session, "mode": mode, "event": decision.event, "fingerprint": fingerprint[:12]},
    )
    if not decision.allow:
        sessions = state.read_json(SESSIONS_FILE, {})
        remember(sessions, session, facts.blocks + 1)
        state.write_json(SESSIONS_FILE, sessions)
        return HookResult(BLOCK, None, f"Commit blocked. {MESSAGES[decision.problem]}")
    if decision.event == "allowed_after_max_blocks":
        note = f"Commit allowed after {facts.blocks} blocked attempts and recorded as unverified. Tell the user why."
        return HookResult(0, hookio.pre_tool_context(note))
    if decision.problem:
        note = f"Commit gate (observe mode): this commit would be blocked in enforce mode. {MESSAGES[decision.problem]}"
        return HookResult(0, hookio.pre_tool_context(note))
    return ALLOW


def main() -> int:
    return hookio.run(evaluate)
