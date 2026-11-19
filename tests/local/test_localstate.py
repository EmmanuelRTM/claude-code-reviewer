"""Local, gitignored state: atomic JSON files and an append-only log."""

from __future__ import annotations

from reviewkit.localstate import LocalState, remember


def test_json_round_trip_and_default(tmp_path):
    state = LocalState(tmp_path)
    assert state.read_json("missing.json", {"a": 1}) == {"a": 1}
    state.write_json("x.json", {"b": [1, 2]})
    assert state.read_json("x.json", None) == {"b": [1, 2]}
    assert (tmp_path / ".claude" / "state" / "x.json").is_file()


def test_corrupt_or_wrong_typed_json_falls_back_to_default(tmp_path):
    state = LocalState(tmp_path)
    state.dir.mkdir(parents=True)
    (state.dir / "x.json").write_text("{nope")
    assert state.read_json("x.json", {}) == {}
    (state.dir / "y.json").write_text("[1]")
    assert state.read_json("y.json", {}) == {}


def test_write_leaves_no_temporary_files(tmp_path):
    state = LocalState(tmp_path)
    state.write_json("x.json", {"a": 1})
    assert sorted(p.name for p in state.dir.iterdir()) == ["x.json"]


def test_log_lines(tmp_path):
    state = LocalState(tmp_path)
    assert state.read_lines("gate.log") == []
    state.append_line("gate.log", {"event": "a"})
    state.append_line("gate.log", {"event": "b"})
    (state.dir / "gate.log").open("a").write("garbage\n")
    assert [e["event"] for e in state.read_lines("gate.log")] == ["a", "b"]


def test_names_cannot_escape_the_state_directory(tmp_path):
    state = LocalState(tmp_path)
    for bad in ("../x.json", "a/b.json", ""):
        try:
            state.write_json(bad, {})
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_remember_keeps_the_most_recent_entries():
    table = {}
    for i in range(60):
        remember(table, f"s{i}", {"n": i}, limit=50)
    assert len(table) == 50 and "s0" not in table and "s59" in table
    remember(table, "s10", {"n": 99}, limit=50)
    assert list(table)[-1] == "s10"
