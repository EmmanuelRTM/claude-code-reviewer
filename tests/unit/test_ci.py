"""GitHub Actions glue: outputs, step summary and event loading."""

from __future__ import annotations

import json

import pytest

from reviewkit import ci


def read_outputs(path):
    """Parse a GITHUB_OUTPUT file (both `k=v` and heredoc forms)."""
    result, lines, i = {}, path.read_text().splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delimiter = line.split("<<", 1)
            i += 1
            chunk = []
            while lines[i] != delimiter:
                chunk.append(lines[i])
                i += 1
            result[key] = "\n".join(chunk)
        else:
            key, _, value = line.partition("=")
            result[key] = value
        i += 1
    return result


def test_set_output_handles_single_and_multi_line_values(tmp_path):
    out = tmp_path / "out"
    env = {"GITHUB_OUTPUT": str(out)}
    ci.set_output("run", "true", env)
    ci.set_output("plan", '{"a":\n1}', env)
    ci.set_output("flag", True, env)
    assert read_outputs(out) == {"run": "true", "plan": '{"a":\n1}', "flag": "true"}


def test_set_output_delimiter_cannot_be_forged(tmp_path):
    out = tmp_path / "out"
    hostile = "line\nghadelimiter_x\nother=1"
    ci.set_output("value", hostile, {"GITHUB_OUTPUT": str(out)})
    assert read_outputs(out) == {"value": hostile}


def test_set_output_rejects_bad_names(tmp_path):
    with pytest.raises(ValueError):
        ci.set_output("bad name", "x", {"GITHUB_OUTPUT": str(tmp_path / "o")})


def test_outputs_and_summary_are_noops_without_the_files():
    ci.set_output("a", "b", {})
    ci.append_summary("# hi", {})


def test_append_summary(tmp_path):
    path = tmp_path / "summary.md"
    env = {"GITHUB_STEP_SUMMARY": str(path)}
    ci.append_summary("# one", env)
    ci.append_summary("two", env)
    assert path.read_text() == "# one\ntwo\n"


def test_load_event(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"action": "opened"}))
    assert ci.load_event({"GITHUB_EVENT_PATH": str(path)}) == {"action": "opened"}
    assert ci.load_event({}) == {}
    path.write_text("not json")
    assert ci.load_event({"GITHUB_EVENT_PATH": str(path)}) == {}


def test_annotations_escape_newlines(capsys):
    ci.warning("first\nsecond % done")
    assert capsys.readouterr().out == "::warning::first%0Asecond %25 done\n"


def test_run_url():
    env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "42"}
    assert ci.run_url(env) == "https://github.com/o/r/actions/runs/42"
    assert ci.run_url({}) == ""
