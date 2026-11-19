"""The manifest format and the per-file decisions made from it."""

from __future__ import annotations

import hashlib
import json

import pytest

from installer.kitfiles import decide_kit_file, decide_removal
from installer.manifest import Manifest, content_hash, parse_manifest, render_manifest, same_content
from installer.report import CREATED, REMOVED, SKIPPED, UNCHANGED, UPDATED


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- hashing -----------------------------------------------------------------


def test_content_hash_is_plain_sha256() -> None:
    assert content_hash(b"print('x')\n") == sha(b"print('x')\n")
    assert content_hash(b"") == sha(b"")


def test_content_hash_ignores_checkout_line_endings() -> None:
    # A Windows checkout with core.autocrlf rewrites LF as CRLF. That is not a local edit.
    assert content_hash(b"a\r\nb\r\n") == content_hash(b"a\nb\n") == sha(b"a\nb\n")


def test_content_hash_leaves_binary_content_alone() -> None:
    binary = b"\x00\x01\r\n\x02"
    assert content_hash(binary) == sha(binary)
    assert content_hash(binary) != content_hash(b"\x00\x01\n\x02")


def test_same_content() -> None:
    assert same_content(b"a\nb\n", b"a\nb\n")
    assert same_content(b"a\r\nb\r\n", b"a\nb\n")
    assert not same_content(b"a\nb\n", b"a\nB\n")
    assert not same_content(b"\x00\r\n", b"\x00\n")


# --- format ------------------------------------------------------------------


def test_render_is_sorted_stable_json_with_a_trailing_newline() -> None:
    manifest = Manifest("1.2.3", {"b.txt": "2" * 64, "a/z.py": "1" * 64})
    text = render_manifest(manifest)
    assert text.endswith("}\n")
    assert json.loads(text) == {"kit_version": "1.2.3", "files": {"a/z.py": "1" * 64, "b.txt": "2" * 64}}
    assert text.index('"a/z.py"') < text.index('"b.txt"')
    assert text.index('"files"') < text.index('"kit_version"')
    assert text == render_manifest(Manifest("1.2.3", {"a/z.py": "1" * 64, "b.txt": "2" * 64}))


def test_parse_round_trips() -> None:
    manifest = Manifest("0.1.0", {".claude/hooks/gate.py": "a" * 64})
    parsed, ignored = parse_manifest(render_manifest(manifest))
    assert parsed == manifest
    assert ignored == ()


@pytest.mark.parametrize("text", ["", "not json", "[]", '"text"', '{"files": []}', '{"files": "x"}'])
def test_parse_rejects_unusable_documents(text: str) -> None:
    with pytest.raises(ValueError):
        parse_manifest(text)


def test_parse_tolerates_a_missing_version_and_files() -> None:
    parsed, ignored = parse_manifest("{}")
    assert parsed == Manifest("0", {})
    assert ignored == ()


def test_parse_drops_entries_it_must_never_act_on() -> None:
    document = {
        "kit_version": "1",
        "files": {
            ".claude/hooks/gate.py": "a" * 64,
            "../outside.txt": "b" * 64,
            "/etc/passwd": "c" * 64,
            ".git/config": "d" * 64,
            ".claude/hooks/bad-hash.py": "xyz",
            ".claude/hooks/not-a-string.py": 12,
        },
    }
    parsed, ignored = parse_manifest(json.dumps(document))
    assert dict(parsed.files) == {".claude/hooks/gate.py": "a" * 64}
    assert set(ignored) == {
        "../outside.txt",
        "/etc/passwd",
        ".git/config",
        ".claude/hooks/bad-hash.py",
        ".claude/hooks/not-a-string.py",
    }


# --- kit-owned files ---------------------------------------------------------

OLD = b"old kit content\n"
NEW = b"new kit content\n"
MINE = b"my local edit\n"


def test_missing_destination_is_created() -> None:
    decision = decide_kit_file(NEW, None, None, force=False)
    assert (decision.status, decision.write, decision.recorded) == (CREATED, True, sha(NEW))


def test_identical_destination_is_unchanged_and_recorded() -> None:
    decision = decide_kit_file(NEW, NEW, None, force=False)
    assert (decision.status, decision.write, decision.recorded) == (UNCHANGED, False, sha(NEW))


def test_untouched_destination_is_updated() -> None:
    decision = decide_kit_file(NEW, OLD, sha(OLD), force=False)
    assert (decision.status, decision.write, decision.recorded) == (UPDATED, True, sha(NEW))


def test_locally_modified_destination_is_skipped_and_keeps_its_record() -> None:
    decision = decide_kit_file(NEW, MINE, sha(OLD), force=False)
    assert (decision.status, decision.write, decision.recorded) == (SKIPPED, False, sha(OLD))


def test_untracked_destination_counts_as_locally_modified() -> None:
    decision = decide_kit_file(NEW, MINE, None, force=False)
    assert (decision.status, decision.write, decision.recorded) == (SKIPPED, False, None)


def test_force_overwrites_local_modifications() -> None:
    for recorded in (sha(OLD), None):
        decision = decide_kit_file(NEW, MINE, recorded, force=True)
        assert (decision.status, decision.write, decision.recorded) == (UPDATED, True, sha(NEW))


def test_a_crlf_checkout_is_not_a_local_modification() -> None:
    crlf_old = OLD.replace(b"\n", b"\r\n")
    assert decide_kit_file(NEW, crlf_old, sha(OLD), force=False).status == UPDATED
    assert decide_kit_file(NEW, NEW.replace(b"\n", b"\r\n"), sha(OLD), force=False).status == UNCHANGED


# --- removals (stale files on upgrade, every kit file on uninstall) -----------


def test_untouched_file_is_removed() -> None:
    decision = decide_removal(OLD, sha(OLD), force=False)
    assert decision is not None
    assert (decision.status, decision.delete, decision.recorded) == (REMOVED, True, None)


def test_modified_file_is_kept_and_stays_recorded() -> None:
    decision = decide_removal(MINE, sha(OLD), force=False)
    assert decision is not None
    assert (decision.status, decision.delete, decision.recorded) == (SKIPPED, False, sha(OLD))


def test_force_removes_a_modified_file() -> None:
    decision = decide_removal(MINE, sha(OLD), force=True)
    assert decision is not None
    assert (decision.status, decision.delete, decision.recorded) == (REMOVED, True, None)


def test_a_file_that_is_already_gone_needs_nothing() -> None:
    assert decide_removal(None, sha(OLD), force=False) is None
