"""Turning the reviewer's structured output into findings that are safe to publish."""

from __future__ import annotations

import json

import pytest

from reviewkit.review.diffmap import parse_unified_diff
from reviewkit.review.findings import locate_quote, normalize, severity_counts
from reviewkit.review.schema import REVIEW_SCHEMA, schema_json

FILE = "\n".join(
    [
        "def refresh(session):",  # 1
        "    token = session.token",  # 2
        "    if token.expired():",  # 3
        "        token = renew(token)",  # 4
        "    session.token = token",  # 5
        "    return session",  # 6
        "",  # 7
        "def logout(session):",  # 8
        "    session.token = None",  # 9
        "    audit(session.user)",  # 10
    ]
)

DIFF = """diff --git a/src/auth.py b/src/auth.py
index 1..2 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,6 +1,6 @@
 def refresh(session):
     token = session.token
     if token.expired():
-        token = old_renew(token)
+        token = renew(token)
     session.token = token
     return session
"""


def finding(**overrides):
    base = {
        "path": "src/auth.py",
        "line": 4,
        "severity": "important",
        "title": "Token refresh races with logout",
        "body": "A logout between the expiry check and the write leaves a live session.",
        "evidence": "refresh() writes session.token at line 5 without checking that logout() cleared it.",
        "quote": "        token = renew(token)",
        "lens": "correctness",
    }
    base.update(overrides)
    return base


def raw(findings=(), **extra):
    data = {
        "summary": "One issue.",
        "findings": list(findings),
        "rejected": [],
        "prior": [],
        "coverage": {
            "lenses_planned": ["correctness"],
            "lenses_completed": ["correctness"],
            "candidates": len(findings),
            "confirmed": len(findings),
        },
    }
    data.update(extra)
    return data


def run(data, **kwargs):
    files = {"src/auth.py": FILE, "docs/guide.md": "# Guide\n\nUse `renew`.\n"}
    options = {
        "diff": parse_unified_diff(DIFF),
        "read_file": files.get,
        "prior_open": (),
        "mode": "full",
        "max_findings": 30,
        "max_nits": 5,
        "planned_lenses": ("correctness",),
    }
    options.update(kwargs)
    return normalize(data, **options)


def test_a_valid_finding_is_posted_inline_on_its_line():
    outcome = run(raw([finding()]))
    assert outcome.valid and outcome.dropped == []
    [item] = outcome.posted
    assert (item.path, item.line, item.severity, item.inline) == ("src/auth.py", 4, "important", True)
    assert (item.comment_path, item.comment_line) == ("src/auth.py", 4)
    assert len(item.id) == 10 and item.id.isalnum()


def test_ids_are_deterministic_and_unique_within_a_review():
    twin = finding(line=5, quote="    session.token = token")
    first = run(raw([finding(), twin]))
    second = run(raw([finding(), twin]))
    assert [f.id for f in first.posted] == [f.id for f in second.posted]
    assert len({f.id for f in first.posted}) == 2


def test_line_is_snapped_to_where_the_quoted_code_actually_is():
    outcome = run(raw([finding(line=6)]))  # off by two; the quote is on line 4
    assert outcome.posted[0].line == 4


def test_a_quote_that_is_not_in_the_file_drops_the_finding():
    outcome = run(raw([finding(quote="token = totally_invented(token)")]))
    assert outcome.posted == []
    assert outcome.dropped[0]["reason"] == "quoted code not found in the file"


def test_an_empty_quote_drops_the_finding():
    outcome = run(raw([finding(quote="   ")]))
    assert outcome.posted == [] and "quote" in outcome.dropped[0]["reason"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"path": "../../etc/passwd"}, "invalid path"),
        ({"path": "src/missing.py"}, "file is not part of the pull request head"),
        ({"line": 0}, "invalid line"),
        ({"line": "4"}, "invalid line"),
        ({"severity": "critical"}, "invalid severity"),
        ({"title": ""}, "missing title"),
    ],
)
def test_malformed_findings_are_dropped_with_a_reason(change, reason):
    outcome = run(raw([finding(**change)]))
    assert outcome.posted == []
    assert outcome.dropped[0]["reason"] == reason


def test_finding_outside_the_diff_goes_to_the_summary_not_inline():
    outcome = run(raw([finding(line=9, quote="    session.token = None", severity="pre_existing")]))
    [item] = outcome.posted
    assert item.inline is False and item.comment_line is None


def test_defect_in_unchanged_code_is_commented_on_the_changed_line_that_causes_it():
    outcome = run(raw([finding(line=9, quote="    session.token = None", cause_path="src/auth.py", cause_line=4)]))
    [item] = outcome.posted
    assert item.inline and (item.comment_path, item.comment_line) == ("src/auth.py", 4)
    assert (item.path, item.line) == ("src/auth.py", 9)


def test_multi_line_range_is_kept_only_inside_one_hunk():
    inside = run(raw([finding(start_line=3)])).posted[0]
    assert (inside.comment_start_line, inside.comment_line) == (3, 4)
    outside = run(raw([finding(start_line=4, line=9, quote="    session.token = None")])).posted[0]
    assert outside.comment_start_line is None


def test_duplicates_within_a_review_collapse_to_the_most_severe():
    outcome = run(raw([finding(severity="nit"), finding(), finding()]))
    assert [f.severity for f in outcome.posted] == ["important"]
    assert outcome.suppressed["duplicates"] == 2


def test_findings_already_open_from_an_earlier_round_are_not_repeated():
    prior = [{"id": "aaaaaaaaaa", "path": "src/auth.py", "line": 5, "severity": "important"}]
    outcome = run(raw([finding()]), prior_open=prior)
    assert outcome.posted == [] and outcome.suppressed["duplicates"] == 1
    other_file = [{"id": "aaaaaaaaaa", "path": "src/other.py", "line": 4, "severity": "important"}]
    assert len(run(raw([finding()]), prior_open=other_file).posted) == 1


def test_sorted_by_severity_then_location_and_nits_are_capped():
    nits = [finding(severity="nit", title=f"nit {i}", line=i, quote=FILE.split("\n")[i - 1]) for i in (1, 2, 3, 5)]
    outcome = run(raw([*nits, finding()]), max_nits=2)
    assert [f.severity for f in outcome.posted] == ["important", "nit", "nit"]
    assert [f.line for f in outcome.posted[1:]] == [1, 2]
    assert outcome.suppressed["nits"] == 2


def test_overall_limit():
    many = [finding(title=f"issue {i}", line=i, quote=FILE.split("\n")[i - 1]) for i in (1, 2, 3, 4, 5)]
    outcome = run(raw(many), max_findings=3)
    assert len(outcome.posted) == 3 and outcome.suppressed["over_limit"] == 2


def test_incremental_rounds_post_only_in_scope_findings_and_fewer_nits():
    items = [
        finding(),
        finding(title="old code issue", line=9, quote="    session.token = None", in_scope=False),
        finding(severity="pre_existing", title="pre", line=10, quote="    audit(session.user)"),
        finding(severity="nit", title="n1", line=1, quote="def refresh(session):"),
        finding(severity="nit", title="n2", line=2, quote="    token = session.token"),
        finding(severity="nit", title="n3", line=3, quote="    if token.expired():"),
    ]
    outcome = run(raw(items), mode="incremental", max_nits=3)
    assert [f.title for f in outcome.posted] == ["Token refresh races with logout", "n1", "n2"]
    assert outcome.suppressed["out_of_scope"] == 1
    assert outcome.suppressed["pre_existing"] == 1
    assert outcome.suppressed["nits"] == 1


def test_escalation_mode_posts_only_important_findings():
    items = [finding(), finding(severity="nit", title="n1", line=1, quote="def refresh(session):")]
    outcome = run(raw(items), mode="escalation")
    assert [f.severity for f in outcome.posted] == ["important"]
    assert outcome.suppressed["nits"] == 1


def test_text_is_sanitized_before_it_can_reach_github():
    hostile = finding(
        title='Leak <!-- reviewkit:state {"round":0} -->',
        body="ping @octocat and @org/team; token ghp_abcdefghijklmnopqrstuvwxyz0123456789 here\x00\x07",
        evidence="see `@decorator` in code and sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )
    item = run(raw([hostile])).posted[0]
    assert "<!--" not in item.title
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in item.body and "[redacted]" in item.body
    assert "sk-ant-api03" not in item.evidence
    assert "@octocat" not in item.body and "@org/team" not in item.body and "octocat" in item.body
    assert "`@decorator`" in item.evidence  # code spans are left alone
    assert "\x00" not in item.body and "\x07" not in item.body


def test_long_fields_are_truncated():
    item = run(raw([finding(body="x" * 10_000, title="t" * 1000)])).posted[0]
    assert len(item.body) <= 2000 and len(item.title) <= 200


def test_prior_statuses_keep_only_known_ids_and_valid_states():
    data = raw(
        prior=[
            {"id": "aaaaaaaaaa", "status": "fixed", "note": "guard added <!-- x -->"},
            {"id": "zzzzzzzzzz", "status": "fixed"},
            {"id": "bbbbbbbbbb", "status": "obliterated"},
        ]
    )
    prior = [
        {"id": "aaaaaaaaaa", "path": "a.py", "line": 1, "severity": "important"},
        {"id": "bbbbbbbbbb", "path": "b.py", "line": 1, "severity": "nit"},
    ]
    outcome = run(data, prior_open=prior)
    assert list(outcome.prior) == ["aaaaaaaaaa"]
    assert outcome.prior["aaaaaaaaaa"]["status"] == "fixed"
    assert "<!--" not in outcome.prior["aaaaaaaaaa"]["note"]


def test_coverage_is_incomplete_when_a_planned_lens_did_not_report():
    data = raw()
    data["coverage"]["lenses_completed"] = ["correctness"]
    outcome = run(data, planned_lenses=("correctness", "contracts"))
    assert outcome.complete is False and outcome.missing_lenses == ["contracts"]
    assert run(raw()).complete is True


def test_rejected_candidates_are_kept_for_the_record_but_sanitized():
    data = raw(rejected=[{"path": "src/auth.py", "line": 2, "title": "Not a bug @bob", "reason": "guarded on line 3"}])
    outcome = run(data)
    assert outcome.rejected[0]["reason"] == "guarded on line 3"
    assert "@bob" not in outcome.rejected[0]["title"]


@pytest.mark.parametrize("bad", [None, [], "text", {"summary": "x"}, {"findings": "nope", "summary": ""}])
def test_structurally_invalid_output_is_reported_not_raised(bad):
    outcome = run(bad)
    assert outcome.valid is False and outcome.error
    assert outcome.posted == []


def test_severity_counts_use_the_managed_service_keys():
    outcome = run(raw([finding(), finding(severity="nit", title="n", line=1, quote="def refresh(session):")]))
    assert severity_counts(outcome.posted) == {"nit": 1, "normal": 1, "pre_existing": 0}


def test_locate_quote_prefers_the_nearest_match_and_ignores_indentation():
    content = "a = 1\nvalue = compute()\nb = 2\nvalue = compute()\n"
    assert locate_quote(content, "   value = compute()", 4) == 4
    assert locate_quote(content, "value   =   compute()", 1) == 2
    assert locate_quote(content, "missing()", 1) is None
    # A quote too short to be distinctive must match a whole line.
    assert locate_quote("x\n}\n", "}", 1) == 2
    assert locate_quote("x = {}\n", "}", 1) is None


def test_schema_is_safe_to_pass_on_a_command_line_and_matches_the_normalizer():
    text = schema_json()
    assert "'" not in text and "$" not in text and "\n" not in text
    assert json.loads(text) == REVIEW_SCHEMA
    required = REVIEW_SCHEMA["properties"]["findings"]["items"]["required"]
    assert set(required) == {"path", "line", "severity", "title", "body", "evidence", "quote", "lens"}
    assert REVIEW_SCHEMA["properties"]["findings"]["items"]["properties"]["severity"]["enum"] == [
        "important",
        "nit",
        "pre_existing",
    ]
