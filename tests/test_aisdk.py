"""The AI SDK protocol layer, and the awkward part of it.

`partial_reply` is the only thing here with anything to get wrong: it reads a
string value out of JSON that is still arriving, one chunk at a time, and the
interesting cases are all about where a chunk boundary lands.
"""

from __future__ import annotations

import json

from app import aisdk


def feed(chunks: list[str]) -> list[str]:
    """Replay chunks the way the router does: emit only what is newly visible."""
    buffer = ""
    shown = 0
    out = []
    for chunk in chunks:
        buffer += chunk
        text = aisdk.partial_reply(buffer)
        if len(text) > shown:
            out.append(text[shown:])
            shown = len(text)
    return out


def test_nothing_until_the_key_arrives():
    assert aisdk.partial_reply("") == ""
    assert aisdk.partial_reply('{"che') == ""
    assert aisdk.partial_reply('{"reply"') == ""
    # The opening quote is what makes the value readable, not the colon.
    assert aisdk.partial_reply('{"reply": ') == ""
    assert aisdk.partial_reply('{"reply": "') == ""


def test_grows_with_the_buffer():
    assert aisdk.partial_reply('{"reply": "Insulin') == "Insulin"
    assert aisdk.partial_reply('{"reply": "Insulin peaks') == "Insulin peaks"


def test_stops_at_the_closing_quote():
    payload = '{"reply": "All done", "wrap_up": true}'
    assert aisdk.partial_reply(payload) == "All done"


def test_reads_past_an_earlier_field():
    payload = '{"wrap_up": false, "reply": "Second in the object"}'
    assert aisdk.partial_reply(payload) == "Second in the object"


def test_escapes_are_decoded_not_shown():
    payload = r'{"reply": "She said \"no\" and left.\nThen a 50\/50 split."}'
    assert aisdk.partial_reply(payload) == 'She said "no" and left.\nThen a 50/50 split.'


def test_an_escaped_quote_does_not_end_the_value():
    assert aisdk.partial_reply(r'{"reply": "a \"quoted\" word') == 'a "quoted" word'


def test_a_split_escape_waits_rather_than_leaking_a_backslash():
    # The chunk ends on the backslash — not raw, so this really is one. Emitting
    # it now means emitting a character that was never in the reply and cannot
    # be taken back.
    assert aisdk.partial_reply('{"reply": "line one\\') == "line one"
    assert feed(['{"reply": "line one\\', 'n and two"']) == ["line one", "\n and two"]
    # A pair, by contrast, is a finished escape for one literal backslash.
    assert aisdk.partial_reply('{"reply": "line one\\\\') == "line one\\"


def test_a_split_unicode_escape_waits_for_all_four_digits():
    assert aisdk.partial_reply(r'{"reply": "caf\u00') == "caf"
    assert aisdk.partial_reply(r'{"reply": "café') == "café"
    assert feed([r'{"reply": "caf\u00', 'e9 time"']) == ["caf", "é time"]


def test_chunking_never_changes_the_text():
    reply = 'A "quoted" line,\na 50/50 split, and a café.'
    payload = json.dumps({"reply": reply, "wrap_up": False})
    for size in (1, 2, 3, 7, 13):
        chunks = [payload[i : i + size] for i in range(0, len(payload), size)]
        assert "".join(feed(chunks)) == reply, f"chunk size {size}"


def test_deltas_are_emitted_once_each():
    payload = json.dumps({"reply": "one two three"})
    chunks = [payload[i : i + 4] for i in range(0, len(payload), 4)]
    assert "".join(feed(chunks)) == "one two three"


def test_a_reply_that_is_only_escapes_still_reads():
    assert aisdk.partial_reply(r'{"reply": "\n\n\t"}') == "\n\n\t"


def test_sse_is_one_json_object_per_line():
    line = aisdk.sse({"type": "text-delta", "id": "m1", "delta": "hi"})
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    assert json.loads(line[6:].strip()) == {"type": "text-delta", "id": "m1", "delta": "hi"}


def test_done_is_the_literal_terminator():
    # Not JSON, deliberately — the protocol ends on a sentinel.
    assert aisdk.done() == "data: [DONE]\n\n"


def test_data_parts_are_namespaced():
    part = json.loads(aisdk.data_part("check", {"question": "?"})[6:].strip())
    assert part == {"type": "data-check", "data": {"question": "?"}}


def test_text_stream_brackets_the_deltas():
    parts = [json.loads(p[6:].strip()) for p in aisdk.text_stream("m1", ["a", "b"])]
    assert [p["type"] for p in parts] == ["text-start", "text-delta", "text-delta", "text-end"]
    assert all(p["id"] == "m1" for p in parts)


def test_the_stream_header_is_the_documented_one():
    assert aisdk.STREAM_HEADER == {"x-vercel-ai-ui-message-stream": "v1"}
