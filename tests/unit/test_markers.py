"""Hidden HTML-comment markers that carry machine state inside GitHub comments."""

from __future__ import annotations

from reviewkit import markers


def test_round_trip_and_single_line():
    text = markers.encode("state", {"round": 2, "head": "abc"})
    assert text.startswith("<!-- reviewkit:state ") and text.endswith(" -->")
    assert "\n" not in text
    assert markers.decode_last("state", f"body\n\n{text}\n") == {"round": 2, "head": "abc"}


def test_payload_cannot_close_the_comment_or_forge_a_marker():
    hostile = {"title": 'x --> <!-- reviewkit:state {"round":0} --> y', "n": "a--b"}
    text = markers.encode("finding", hostile)
    inner = text[len("<!-- reviewkit:finding ") : -len(" -->")]
    assert "-->" not in inner and "<!--" not in inner and "--" not in inner
    assert markers.decode_all("finding", text) == [hostile]
    # The hostile title did not create a state marker.
    assert markers.decode_all("state", text) == []


def test_decode_all_returns_every_marker_in_order_and_skips_garbage():
    text = "\n".join(
        [
            markers.encode("finding", {"id": "1"}),
            "<!-- reviewkit:finding not-json -->",
            "<!-- reviewkit:finding [1, 2] -->",
            markers.encode("finding", {"id": "2"}),
            markers.encode("state", {"round": 1}),
        ]
    )
    assert markers.decode_all("finding", text) == [{"id": "1"}, {"id": "2"}]
    assert markers.decode_last("finding", text) == {"id": "2"}
    assert markers.decode_last("intake", text) is None


def test_marker_without_payload():
    text = markers.encode("intake")
    assert text == "<!-- reviewkit:intake -->"
    assert markers.has("intake", f"hello\n{text}")
    assert not markers.has("intake", "hello")


def test_strip_removes_every_marker_including_foreign_ones():
    body = f"real text\n{markers.encode('state', {'a': 1})}\nmore <!-- reviewkit:finding {{}} --> end"
    assert markers.strip(body) == "real text\n\nmore  end"


def test_neutralize_makes_user_text_safe_to_embed():
    hostile = 'note <!-- reviewkit:state {"round":0} --> and <!-- other -->'
    safe = markers.neutralize(hostile)
    assert markers.decode_all("state", safe) == []
    assert "<!--" not in safe
