"""CLI for the local loop: fingerprint, verification marker, checks, gate report."""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .checks import configured_checks, format_failure, run_checks
from .config import ConfigError, load_config
from .fingerprint import FingerprintError, tree_fingerprint
from .gitio import repo_root
from .hooks import commit_gate, stop_checks
from .localstate import LocalState, utc_now

_EVIDENCE_LIMIT = 500


def _project(args) -> Path:
    start = Path(getattr(args, "project", None) or ".").resolve()
    return repo_root(start) or start


def _fail(message: str) -> int:
    print(f"reviewkit: {message}", file=sys.stderr)
    return 1


def _fingerprint(args) -> int:
    try:
        print(tree_fingerprint(_project(args)))
    except FingerprintError as exc:
        return _fail(str(exc))
    return 0


def _verify_mark(args) -> int:
    project = _project(args)
    try:
        fingerprint = tree_fingerprint(project)
    except FingerprintError as exc:
        return _fail(str(exc))
    LocalState(project).write_json(
        commit_gate.MARKER_FILE,
        {"fingerprint": fingerprint, "ts": utc_now(), "evidence": (args.evidence or "")[:_EVIDENCE_LIMIT]},
    )
    print(f"verified {fingerprint[:12]}: this exact tree is recorded as verified")
    return 0


def _verify_status(args) -> int:
    project = _project(args)
    try:
        fingerprint = tree_fingerprint(project)
    except FingerprintError as exc:
        return _fail(str(exc))
    marker = LocalState(project).read_json(commit_gate.MARKER_FILE, {})
    if marker.get("fingerprint") == fingerprint:
        print(f"verified {fingerprint[:12]} at {marker.get('ts', 'unknown time')}")
        return 0
    print(f"not verified: the current tree {fingerprint[:12]} has no matching verification")
    return 1


def _checks_run(args) -> int:
    project = _project(args)
    try:
        cfg = load_config(project).config
    except ConfigError as exc:
        return _fail(str(exc))
    if not configured_checks(cfg, args.names):
        print("No checks are configured. Set commands.test, commands.lint or commands.build in .claude/reviewkit.json.")
        return 0
    status = 0
    for result in run_checks(cfg, project, args.names or None):
        print(f"{result.name}: {result.status} ({result.duration:.1f}s)")
        if not result.ok:
            print(format_failure(result))
            status = 1
    return status


def _gate_report(args) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    entries = []
    for entry in LocalState(_project(args)).read_lines(commit_gate.LOG_FILE):
        try:
            when = datetime.strptime(str(entry.get("ts")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when >= since or args.all:
            entries.append(entry)
    if not entries:
        print(f"No commits were seen by the gate in the last {args.days} days.")
        return 0
    counts = Counter(str(entry.get("event")) for entry in entries)
    print(f"Commit gate: {len(entries)} commits in the last {args.days} days")
    for event, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count:4d}  {event}")
    return 0


def register(subparsers) -> None:
    fingerprint = subparsers.add_parser("fingerprint", help="print the working-tree fingerprint")
    fingerprint.add_argument("--project")
    fingerprint.set_defaults(handler=_fingerprint)

    verify = subparsers.add_parser("verify", help="verification marker for the commit gate")
    verify_sub = verify.add_subparsers(dest="verify_command", metavar="<action>")
    mark = verify_sub.add_parser("mark", help="record that the current tree was verified end-to-end")
    mark.add_argument("--evidence", default="", help="one line naming what was run and observed")
    mark.add_argument("--project")
    mark.set_defaults(handler=_verify_mark)
    status = verify_sub.add_parser("status", help="exit 0 when the current tree is verified")
    status.add_argument("--project")
    status.set_defaults(handler=_verify_status)

    checks = subparsers.add_parser("checks", help="the project's lint, build and test commands")
    checks_sub = checks.add_subparsers(dest="checks_command", metavar="<action>")
    run = checks_sub.add_parser("run", help="run the configured checks, stopping at the first failure")
    run.add_argument("names", nargs="*", choices=["lint", "build", "test", []], help="subset to run")
    run.add_argument("--project")
    run.set_defaults(handler=_checks_run)

    gate = subparsers.add_parser("gate", help="commit gate log")
    gate_sub = gate.add_subparsers(dest="gate_command", metavar="<action>")
    report = gate_sub.add_parser("report", help="summarize what the gate saw (share this while in observe mode)")
    report.add_argument("--days", type=int, default=7)
    report.add_argument("--all", action="store_true", help="ignore --days")
    report.add_argument("--project")
    report.set_defaults(handler=_gate_report)

    hook = subparsers.add_parser("hook", help="run a hook (reads the event from stdin)")
    hook_sub = hook.add_subparsers(dest="hook_command", metavar="<hook>")
    hook_sub.add_parser("commit-gate").set_defaults(handler=lambda _args: commit_gate.main())
    hook_sub.add_parser("stop-checks").set_defaults(handler=lambda _args: stop_checks.main())
