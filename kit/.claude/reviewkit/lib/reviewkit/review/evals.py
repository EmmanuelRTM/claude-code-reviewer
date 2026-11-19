"""Review the reviewer: score it against bugs you already know about.

A case is a past change (two commits of this repository) plus an answer key:
where the bug was. Control cases are merged, stable changes on which nothing
Important should be reported. Recall is measured at three stages, so you can
see where a bug got lost: never found, rejected by verification, or cut by the
caps and the quote check before publishing.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from ..config import load_config
from ..gitio import git
from ..paths import match_any
from .context import Workspace, build_context, read_blob
from .diffmap import parse_unified_diff, unified_diff
from .findings import Outcome, normalize
from .plan import Plan, plan_lenses
from .session import claude_args, run_claude

LINE_SLACK = 3
Session = Callable[[str, str, Path], Optional[dict]]


@dataclass(frozen=True)
class Expected:
    path: str
    first: int
    last: int
    summary: str = ""


@dataclass(frozen=True)
class EvalCase:
    name: str
    base: str
    head: str
    control: bool = False
    expected: tuple = ()


@dataclass(frozen=True)
class CaseScore:
    name: str
    control: bool
    expected: int
    found_raw: int
    found_verified: int
    found_final: int
    unexpected_important: int
    completed: bool
    cost_usd: float = 0.0


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def _matches(expected: Expected, item: Any) -> bool:
    for path_key, line_key in (("path", "line"), ("cause_path", "cause_line")):
        path, line = _field(item, path_key), _field(item, line_key)
        near = isinstance(line, int) and expected.first - LINE_SLACK <= line <= expected.last + LINE_SLACK
        if path == expected.path and near:
            return True
    return False


def score_case(case: EvalCase, raw: Optional[Mapping], outcome: Optional[Outcome], cost_usd: float = 0.0) -> CaseScore:
    if not isinstance(raw, Mapping):
        return CaseScore(case.name, case.control, len(case.expected), 0, 0, 0, 0, False, cost_usd)
    confirmed = [item for item in raw.get("findings") or [] if isinstance(item, Mapping)]
    rejected = [item for item in raw.get("rejected") or [] if isinstance(item, Mapping)]
    posted = list(outcome.posted) if outcome is not None else []
    verified = sum(any(_matches(e, item) for item in confirmed) for e in case.expected)
    found = sum(any(_matches(e, item) for item in confirmed + rejected) for e in case.expected)
    final = sum(any(_matches(e, item) for item in posted) for e in case.expected)
    noise = sum(
        1 for item in posted if item.severity == "important" and not any(_matches(e, item) for e in case.expected)
    )
    return CaseScore(case.name, case.control, len(case.expected), found, verified, final, noise, True, cost_usd)


def aggregate(scores: Sequence[CaseScore]) -> dict:
    bugs = sum(score.expected for score in scores if not score.control)
    controls = [score for score in scores if score.control]

    def rate(attribute: str) -> Optional[float]:
        return sum(getattr(s, attribute) for s in scores if not s.control) / bugs if bugs else None

    return {
        "runs": len(scores),
        "bugs": bugs,
        "recall_raw": rate("found_raw"),
        "recall_verified": rate("found_verified"),
        "recall_final": rate("found_final"),
        "unexpected_important": sum(score.unexpected_important for score in scores if not score.control),
        "control_cases": len(controls),
        "controls_flagged": sum(1 for score in controls if score.unexpected_important),
        "incomplete": sum(1 for score in scores if not score.completed),
        "cost_usd": sum(score.cost_usd for score in scores),
    }


def _expected_from(entry: Any, where: str) -> Expected:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        raise ValueError(f"{where}: each expected finding needs a path")
    lines = entry.get("lines")
    if isinstance(lines, int):
        lines = [lines, lines]
    if not (isinstance(lines, list) and len(lines) == 2 and all(isinstance(n, int) for n in lines)):
        raise ValueError(f"{where}: lines must be a number or [first, last]")
    return Expected(entry["path"], min(lines), max(lines), str(entry.get("summary") or ""))


def load_cases(directory: Path) -> List[EvalCase]:
    """Read `<directory>/<case>/case.json` files, validating each one."""
    cases: List[EvalCase] = []
    for path in sorted(Path(directory).glob("*/case.json")):
        where = path.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{where}: not valid JSON ({exc})") from exc
        for key in ("name", "base", "head"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f"{where}: missing {key}")
        control = data.get("control") is True
        expected = tuple(_expected_from(entry, where) for entry in data.get("expected") or [])
        if not control and not expected:
            raise ValueError(f"{where}: a bug case needs at least one expected finding (or set control to true)")
        cases.append(EvalCase(data["name"], data["base"], data["head"], control, expected))
    return cases


def _percent(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render(scores: Sequence[CaseScore], summary: Mapping[str, Any], level: str, runs: int) -> str:
    lines = [
        f"Reviewer eval: level {level}, {runs} run(s) per case",
        "",
        "| Case | Kind | Known bugs | Found | Confirmed | Published | Unexpected Important |",
        "| - | - | - | - | - | - | - |",
    ]
    for score in scores:
        kind = "control" if score.control else "bug"
        status = "" if score.completed else " (no result)"
        lines.append(
            f"| {score.name}{status} | {kind} | {score.expected} | {score.found_raw} | {score.found_verified} "
            f"| {score.found_final} | {score.unexpected_important} |"
        )
    lines += [
        "",
        f"Recall: found {_percent(summary['recall_raw'])}, confirmed {_percent(summary['recall_verified'])}, "
        f"published {_percent(summary['recall_final'])} of {summary['bugs']} known bugs across runs.",
        f"Controls flagged with an Important finding: {summary['controls_flagged']} of {summary['control_cases']}.",
        f"Sessions without a result: {summary['incomplete']}. Estimated cost: ${summary['cost_usd']:.2f}.",
    ]
    return "\n".join(lines)


def run_eval(
    repo: Path,
    cases: Sequence[EvalCase],
    *,
    level: str,
    runs: int,
    workdir: Path,
    session: Session = run_claude,
) -> List[CaseScore]:
    """Review each case through the same context builder and session arguments as CI."""
    cfg = load_config(repo).config
    cfg = dataclasses.replace(cfg, review=dataclasses.replace(cfg.review, level=level))
    rules_commit = git(["rev-parse", "HEAD"], repo).strip()
    scores: List[CaseScore] = []
    for case in cases:
        head = git(["rev-parse", f"{case.head}^{{commit}}"], repo).strip()
        merge_base = git(["merge-base", case.base, head], repo).strip()
        diff_text = unified_diff(repo, merge_base, head)
        files = parse_unified_diff(diff_text)
        touched = {path for entry in files.values() for path in (entry.path, entry.old_path)}
        changed_lines = sum(entry.additions + entry.deletions for entry in files.values())
        high_risk = any(match_any(cfg.risk.high_paths, path) for path in touched)
        plan = Plan(
            run=True, reason="eval", head_sha=head, merge_base=merge_base, mode="full", round=1, level=level,
            lenses=plan_lenses(level, changed_lines, high_risk), publish="dry_run", manual=True,
        )  # fmt: skip
        for run in range(1, runs + 1):
            workspace = Workspace(Path(workdir) / f"{case.name}-{run}")
            build_context(
                repo=repo, workspace=workspace, cfg=cfg, plan=plan, repo_name=repo.name, rules_commit=rules_commit,
                files=files, diff_text=diff_text, threads=[],
            )  # fmt: skip
            result = session(f"/pr-review {workspace.context_file}", claude_args(cfg, workspace), repo) or {}
            raw = result.get("structured_output") if isinstance(result.get("structured_output"), dict) else None
            outcome = None
            if raw is not None:
                outcome = normalize(
                    raw,
                    diff=files,
                    read_file=lambda path, head=head: read_blob(repo, head, path),
                    prior_open=[],
                    mode="full",
                    max_findings=cfg.review.max_findings,
                    max_nits=cfg.review.max_nits,
                    planned_lenses=plan.lenses,
                )
            cost = result.get("total_cost_usd") if isinstance(result.get("total_cost_usd"), (int, float)) else 0.0
            scores.append(score_case(case, raw, outcome, float(cost)))
    return scores
