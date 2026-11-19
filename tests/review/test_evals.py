"""Scoring a review against known bugs: recall at each stage, and noise on clean changes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reviewkit.review import evals
from reviewkit.review.findings import Finding, Outcome


def posted(path="src/a.py", line=12, severity="important"):
    return Finding(
        id=f"{path}{line}",
        path=path,
        line=line,
        severity=severity,
        title="t",
        body="",
        evidence="",
        quote="q",
        lens="correctness",
    )


CASE = evals.EvalCase(
    name="double-refund",
    base="b",
    head="h",
    control=False,
    expected=(
        evals.Expected("src/a.py", 10, 14, "refund retried without an idempotency key"),
        evals.Expected("src/b.py", 40, 40, "token logged"),
    ),
)


def test_recall_is_measured_at_three_stages():
    raw = {
        "findings": [{"path": "src/a.py", "line": 12}, {"path": "src/z.py", "line": 1}],
        "rejected": [{"path": "src/b.py", "line": 41}],
    }
    outcome = Outcome(posted=[posted("src/a.py", 12), posted("src/z.py", 1)])
    score = evals.score_case(CASE, raw, outcome)
    # Bug 1 was found, confirmed and published. Bug 2 was found but rejected by the verifier.
    assert (score.found_raw, score.found_verified, score.found_final) == (2, 1, 1)
    assert score.expected == 2
    assert score.unexpected_important == 1  # src/z.py matches no known bug


def test_line_slack_and_cause_location():
    near = evals.score_case(CASE, {"findings": [{"path": "src/a.py", "line": 17}], "rejected": []}, Outcome())
    assert near.found_verified == 1  # within three lines of the expected range
    far = evals.score_case(CASE, {"findings": [{"path": "src/a.py", "line": 30}], "rejected": []}, Outcome())
    assert far.found_verified == 0
    by_cause = {
        "findings": [{"path": "src/other.py", "line": 5, "cause_path": "src/b.py", "cause_line": 40}],
        "rejected": [],
    }
    assert evals.score_case(CASE, by_cause, Outcome()).found_verified == 1


def test_a_session_that_returned_nothing_scores_zero_not_an_error():
    score = evals.score_case(CASE, None, None)
    assert (score.found_raw, score.found_verified, score.found_final, score.completed) == (0, 0, 0, False)


def test_control_cases_count_published_important_findings_as_noise():
    control = evals.EvalCase(name="rename", base="b", head="h", control=True, expected=())
    noisy = evals.score_case(
        control, {"findings": [], "rejected": []}, Outcome(posted=[posted(), posted(severity="nit")])
    )
    assert noisy.unexpected_important == 1 and noisy.expected == 0


def test_aggregate_reports_rates_and_never_divides_by_zero():
    scores = [
        evals.CaseScore("a", False, 2, 2, 1, 1, 0, True, 0.5),
        evals.CaseScore("b", False, 1, 1, 1, 1, 2, True, 0.25),
        evals.CaseScore("c", True, 0, 0, 0, 0, 1, True, 0.1),
        evals.CaseScore("d", True, 0, 0, 0, 0, 0, True, 0.1),
    ]
    summary = evals.aggregate(scores)
    assert summary["bugs"] == 3
    assert summary["recall_raw"] == 1.0
    assert summary["recall_verified"] == pytest.approx(2 / 3)
    assert summary["recall_final"] == pytest.approx(2 / 3)
    assert summary["control_cases"] == 2 and summary["controls_flagged"] == 1
    assert summary["cost_usd"] == pytest.approx(0.95)
    empty = evals.aggregate([])
    assert empty["recall_final"] is None and empty["controls_flagged"] == 0


def test_load_cases_validates_the_files(tmp_path):
    good = tmp_path / "one"
    good.mkdir()
    (good / "case.json").write_text(
        json.dumps(
            {
                "name": "one",
                "base": "abc",
                "head": "def",
                "expected": [{"path": "src/a.py", "lines": [10, 14], "summary": "bug"}],
            }
        )
    )
    control = tmp_path / "two"
    control.mkdir()
    (control / "case.json").write_text(json.dumps({"name": "two", "base": "abc", "head": "def", "control": True}))
    cases = evals.load_cases(tmp_path)
    assert [c.name for c in cases] == ["one", "two"]
    assert cases[0].expected[0] == evals.Expected("src/a.py", 10, 14, "bug")
    assert cases[1].control and cases[1].expected == ()

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "case.json").write_text(json.dumps({"name": "bad", "base": "abc"}))
    with pytest.raises(ValueError) as err:
        evals.load_cases(tmp_path)
    assert "bad" in str(err.value) and "head" in str(err.value)


def test_a_bug_case_needs_at_least_one_expected_finding(tmp_path):
    case = tmp_path / "x"
    case.mkdir()
    (case / "case.json").write_text(json.dumps({"name": "x", "base": "a", "head": "b"}))
    with pytest.raises(ValueError):
        evals.load_cases(tmp_path)


def test_render_table():
    scores = [evals.CaseScore("double-refund", False, 2, 2, 1, 1, 0, True, 0.5)]
    text = evals.render(scores, evals.aggregate(scores), level="standard", runs=1)
    assert "double-refund" in text and "recall" in text.lower() and "standard" in text


def test_run_eval_uses_the_same_pipeline_as_ci(repo, tmp_path):
    repo.write("REVIEW.md", "rules\n")
    repo.write("src/app.py", "def total(items):\n    return sum(items)\n")
    base = repo.commit("base")
    repo.write("src/app.py", "def total(items):\n    return sum(items) / len(items)\n")
    head = repo.commit("bug")
    case = evals.EvalCase("avg", base, head, False, (evals.Expected("src/app.py", 2, 2, "division by zero"),))
    seen = {}

    def fake_session(prompt, arguments, cwd):
        seen["prompt"], seen["arguments"], seen["cwd"] = prompt, arguments, cwd
        context = json.loads(Path(prompt.split(" ", 1)[1]).read_text(encoding="utf-8"))
        assert context["lenses"] == [{"id": "all", "model": "sonnet"}]
        return {
            "structured_output": {
                "summary": "s",
                "rejected": [],
                "prior": [],
                "findings": [
                    {
                        "path": "src/app.py",
                        "line": 2,
                        "severity": "important",
                        "title": "Division by zero",
                        "body": "b",
                        "evidence": "e",
                        "quote": "return sum(items) / len(items)",
                        "lens": "all",
                    }
                ],
                "coverage": {"lenses_planned": ["all"], "lenses_completed": ["all"], "candidates": 1, "confirmed": 1},
            },
            "total_cost_usd": 0.42,
        }

    scores = evals.run_eval(repo.path, [case], level="quick", runs=2, session=fake_session, workdir=tmp_path / "w")
    assert len(scores) == 2
    assert all((s.found_raw, s.found_verified, s.found_final) == (1, 1, 1) for s in scores)
    assert scores[0].cost_usd == 0.42
    assert seen["prompt"].startswith("/pr-review ") and "--json-schema" in seen["arguments"]
    assert seen["cwd"] == repo.path
