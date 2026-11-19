"""The printed report: one line per path, warnings, errors and next steps."""

from __future__ import annotations

from pathlib import Path

from installer.report import (
    CREATED,
    ERROR,
    KEPT,
    MERGED,
    REMOVED,
    SKIPPED,
    UNCHANGED,
    UPDATED,
    Entry,
    Report,
    format_report,
    next_steps,
)


def make_report(**changes) -> Report:
    values = {
        "mode": "install",
        "target": Path("/work/repo"),
        "kit_version": "1.2.3",
        "dry_run": False,
        "entries": [
            Entry(".claude/hooks/gate.py", CREATED),
            Entry(".claude/reviewkit/rk.py", UPDATED),
            Entry(".claude/agents/bug-finder.md", UNCHANGED),
            Entry(".claude/settings.json", MERGED),
            Entry("REVIEW.md", KEPT),
        ],
        "warnings": [],
    }
    values.update(changes)
    return Report(**values)


def test_status_words_are_the_documented_ones() -> None:
    assert (CREATED, UPDATED, UNCHANGED, MERGED) == ("created", "updated", "unchanged", "merged")
    assert (SKIPPED, REMOVED, KEPT) == ("skipped (locally modified)", "removed", "kept (user-owned)")


def test_one_line_per_path() -> None:
    lines = format_report(make_report()).splitlines()
    for path, status in [
        (".claude/hooks/gate.py", "created"),
        (".claude/reviewkit/rk.py", "updated"),
        (".claude/agents/bug-finder.md", "unchanged"),
        (".claude/settings.json", "merged"),
        ("REVIEW.md", "kept (user-owned)"),
    ]:
        matching = [line for line in lines if line.split()[-1:] == [path]]
        assert len(matching) == 1, path
        assert matching[0].strip().startswith(status)


def test_header_names_the_version_and_target() -> None:
    text = format_report(make_report())
    assert "1.2.3" in text.splitlines()[0]
    assert "/work/repo" in text.splitlines()[0]


def test_queries() -> None:
    report = make_report()
    assert report.ok
    assert report.status_of("REVIEW.md") == KEPT
    assert report.status_of("missing") is None
    assert report.paths(CREATED) == [".claude/hooks/gate.py"]
    assert report.changed is True
    quiet = make_report(entries=[Entry("a", UNCHANGED), Entry("b", KEPT)])
    assert quiet.changed is False


def test_next_steps_carry_the_required_facts() -> None:
    text = "\n".join(next_steps())
    assert len(next_steps()) == 5
    for fact in [
        "branch",
        "pull request",
        "default branch",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "claude setup-token",
        "never commit",
        "https://github.com/apps/claude",
        "@claude",
        "dismiss stale approvals",
        "conversation resolution",
        "owners",
        "python3 -I .claude/reviewkit/rk.py doctor",
    ]:
        assert fact in text, fact


def test_a_successful_install_ends_with_next_steps() -> None:
    text = format_report(make_report())
    assert "Next steps" in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text
    assert text.endswith("\n")


def test_a_dry_run_says_so_and_skips_next_steps() -> None:
    text = format_report(make_report(dry_run=True))
    assert "dry run" in text.lower()
    assert "Next steps" not in text


def test_errors_are_listed_with_their_reason_and_suppress_next_steps() -> None:
    report = make_report(entries=[Entry("CLAUDE.md", ERROR, "begin marker without an end marker")])
    text = format_report(report)
    assert not report.ok
    assert [entry.path for entry in report.errors] == ["CLAUDE.md"]
    assert "CLAUDE.md: begin marker without an end marker" in text
    assert "Next steps" not in text
    assert "run the installer again" in text


def test_details_follow_the_path() -> None:
    text = format_report(make_report(entries=[Entry(".gitignore", UPDATED, "managed block removed")]))
    assert ".gitignore (managed block removed)" in text


def test_warnings_are_printed() -> None:
    text = format_report(make_report(warnings=["code owners are not set"]))
    assert "Warnings" in text
    assert "code owners are not set" in text


def test_skipped_files_come_with_a_hint_about_force() -> None:
    text = format_report(make_report(entries=[Entry(".claude/hooks/gate.py", SKIPPED)]))
    assert "--force" in text


def test_uninstall_report_has_no_next_steps_and_explains_what_stays() -> None:
    report = make_report(mode="uninstall", entries=[Entry("x.py", REMOVED), Entry("REVIEW.md", KEPT)])
    text = format_report(report)
    assert "Next steps" not in text
    assert "left in place" in text
