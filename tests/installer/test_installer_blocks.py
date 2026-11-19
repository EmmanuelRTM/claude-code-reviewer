"""Managed blocks: create, append, replace in place, remove, and refuse malformed markers."""

from __future__ import annotations

import pytest

from installer.blocks import HASH_MARKERS, HTML_MARKERS, BlockError, Markers, remove_block, upsert_block

BLOCK = "# reviewkit:begin\nrule one\n# reviewkit:end\n"
OLD_BLOCK = "# reviewkit:begin\nold rule\nolder rule\n# reviewkit:end\n"
HTML_BLOCK = "<!-- reviewkit:begin -->\n## Contract\n<!-- reviewkit:end -->\n"


def test_marker_pairs() -> None:
    assert Markers("<!-- reviewkit:begin -->", "<!-- reviewkit:end -->") == HTML_MARKERS
    assert Markers("# reviewkit:begin", "# reviewkit:end") == HASH_MARKERS


# --- upsert ------------------------------------------------------------------


def test_an_absent_file_becomes_just_the_block() -> None:
    assert upsert_block(None, BLOCK, HASH_MARKERS) == BLOCK
    assert upsert_block(None, HTML_BLOCK, HTML_MARKERS) == HTML_BLOCK


def test_blank_lines_around_the_template_are_trimmed() -> None:
    assert upsert_block(None, "\n\n" + BLOCK + "\n\n", HASH_MARKERS) == BLOCK
    assert upsert_block(None, BLOCK.rstrip("\n"), HASH_MARKERS) == BLOCK


@pytest.mark.parametrize(
    "existing",
    ["node_modules/", "node_modules/\n", "node_modules/\n\n\n", "node_modules/\n  \n\t\n"],
)
def test_the_block_is_appended_after_exactly_one_blank_line(existing: str) -> None:
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == "node_modules/\n\n" + BLOCK


def test_appending_keeps_existing_lines_byte_for_byte() -> None:
    existing = "# Title  \n\n  indented\ttext   \nlast line with trailing spaces   \n"
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == existing + "\n" + BLOCK


@pytest.mark.parametrize("existing", ["", "\n", "  \n\n"])
def test_an_empty_file_gets_just_the_block(existing: str) -> None:
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == BLOCK


def test_an_existing_block_is_replaced_in_place() -> None:
    existing = "top\n\n" + OLD_BLOCK + "\nbottom\n"
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == "top\n\n" + BLOCK + "\nbottom\n"


def test_content_outside_the_block_is_untouched() -> None:
    before = "  odd   spacing\t\n\n\n\n"
    after = "\n\n\ntrailing  \n\n\n"
    assert upsert_block(before + OLD_BLOCK + after, BLOCK, HASH_MARKERS) == before + BLOCK + after


def test_replacing_with_the_same_block_changes_nothing() -> None:
    text = "top\n\n" + BLOCK + "\nbottom\n"
    assert upsert_block(text, BLOCK, HASH_MARKERS) == text


@pytest.mark.parametrize("existing", [None, "", "a\n", "a", "a\n\n\n", "a\n\n" + OLD_BLOCK + "b\n", "x\r\ny\r\n"])
def test_upsert_is_idempotent(existing) -> None:
    once = upsert_block(existing, BLOCK, HASH_MARKERS)
    assert upsert_block(once, BLOCK, HASH_MARKERS) == once


def test_a_block_ending_the_file_without_a_newline_is_replaced() -> None:
    existing = "a\n\n# reviewkit:begin\nold\n# reviewkit:end"
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == "a\n\n" + BLOCK


def test_padded_marker_lines_are_recognised() -> None:
    existing = "a\n  # reviewkit:begin  \nold\n\t# reviewkit:end\nb\n"
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == "a\n" + BLOCK + "b\n"


def test_a_byte_order_mark_does_not_hide_the_first_marker() -> None:
    existing = "﻿# reviewkit:begin\nold\n# reviewkit:end\n"
    assert upsert_block(existing, BLOCK, HASH_MARKERS).count("# reviewkit:begin") == 1


def test_marker_text_inside_a_longer_line_is_not_a_marker() -> None:
    existing = "Write `# reviewkit:begin` on its own line to start a block.\n"
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == existing + "\n" + BLOCK


@pytest.mark.parametrize(
    "existing",
    [
        "a\n# reviewkit:begin\nrule\n",  # begin without end
        "a\nrule\n# reviewkit:end\n",  # end without begin
        "# reviewkit:end\nrule\n# reviewkit:begin\n",  # wrong order
        BLOCK + "\n" + BLOCK,  # two pairs
        "# reviewkit:begin\n# reviewkit:begin\nrule\n# reviewkit:end\n",  # two begins
        "# reviewkit:begin\nrule\n# reviewkit:end\n# reviewkit:end\n",  # two ends
    ],
)
def test_malformed_markers_are_refused(existing: str) -> None:
    with pytest.raises(BlockError):
        upsert_block(existing, BLOCK, HASH_MARKERS)
    with pytest.raises(BlockError):
        remove_block(existing, HASH_MARKERS)


@pytest.mark.parametrize(
    "template",
    [
        "no markers at all\n",
        "# reviewkit:begin\nno end\n",
        "preamble\n# reviewkit:begin\nrule\n# reviewkit:end\n",
        "# reviewkit:begin\nrule\n# reviewkit:end\npostscript\n",
        "# reviewkit:begin\n# reviewkit:end\nrule\n# reviewkit:end\n",
    ],
)
def test_a_template_must_be_exactly_one_marked_block(template: str) -> None:
    with pytest.raises(BlockError):
        upsert_block(None, template, HASH_MARKERS)


def test_markers_of_the_other_kind_are_ordinary_text() -> None:
    existing = "<!-- reviewkit:begin -->\nhtml flavoured\n"
    assert upsert_block(existing, BLOCK, HASH_MARKERS) == existing + "\n" + BLOCK


def test_crlf_files_keep_their_line_endings() -> None:
    crlf_block = BLOCK.replace("\n", "\r\n")
    appended = upsert_block("a\r\nb\r\n", BLOCK, HASH_MARKERS)
    assert appended == "a\r\nb\r\n\r\n" + crlf_block
    replaced = upsert_block("a\r\n\r\n" + OLD_BLOCK.replace("\n", "\r\n") + "z\r\n", BLOCK, HASH_MARKERS)
    assert replaced == "a\r\n\r\n" + crlf_block + "z\r\n"


# --- remove ------------------------------------------------------------------


def test_remove_closes_the_gap_to_one_blank_line() -> None:
    assert remove_block("top\n\n" + BLOCK + "\nbottom\n", HASH_MARKERS) == "top\n\nbottom\n"
    assert remove_block("top\n\n\n" + BLOCK + "\n\n\nbottom\n", HASH_MARKERS) == "top\n\nbottom\n"
    assert remove_block("top\n" + BLOCK + "bottom\n", HASH_MARKERS) == "top\n\nbottom\n"


def test_remove_a_block_at_the_end() -> None:
    assert remove_block("top\n\n" + BLOCK, HASH_MARKERS) == "top\n"


def test_remove_a_block_at_the_start() -> None:
    assert remove_block(BLOCK + "\nbottom\n", HASH_MARKERS) == "bottom\n"


def test_removing_the_only_content_leaves_an_empty_file() -> None:
    assert remove_block(BLOCK, HASH_MARKERS) == ""


def test_remove_without_a_block_returns_the_text_unchanged() -> None:
    text = "no block here\n\n\n"
    assert remove_block(text, HASH_MARKERS) == text


@pytest.mark.parametrize("existing", ["a\n", "a\nb\n\nc\n", "", "a\r\nb\r\n", "# Title  \n\ntext\n"])
def test_install_then_uninstall_restores_the_file(existing: str) -> None:
    assert remove_block(upsert_block(existing, BLOCK, HASH_MARKERS), HASH_MARKERS) == existing


def test_remove_keeps_content_outside_the_block_byte_for_byte() -> None:
    before = "  odd   spacing\t\nline\n"
    after = "after  \n\n\n"
    assert remove_block(before + "\n" + BLOCK + "\n" + after, HASH_MARKERS) == before + "\n" + after
