"""app/llm.py talks to a real network, so everywhere else in the app keeps its
prompt logic separate specifically so it doesn't need this file's help to be
tested. What's tested here is the client's own behaviour — building the
request and parsing the reply — with the network call itself faked out.

Written after a real incident: a chat reply was silently truncated by the
provider's own token default (nothing in the request set one), which left the
JSON with no closing brace and surfaced to the reader as an unrelated-looking
503. These pin the fix — a `max_tokens` on every request, generous enough for
the worst case the schemas allow, and a distinct error when a reply is cut off
so the log says what actually happened instead of "not JSON".
"""

import httpx
import pytest

from app import llm
from app.config import get_settings


def _response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", llm._ENDPOINT))


def _reply(content: str, finish_reason: str = "stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


class TestMaxTokens:
    def test_every_request_sets_a_token_ceiling(self, monkeypatch):
        # The bug itself: nothing capped this before, so the provider's own
        # (smaller) default applied and a full reply-plus-check could be cut off
        # mid-object with no way to tell it apart from a malformed one.
        captured = {}

        def fake_post(url, *, json, **kwargs):
            captured.update(json)
            return _response(_reply('{"reply": "hi"}'))

        monkeypatch.setattr(httpx, "post", fake_post)
        llm.complete_json("sys", "user")
        assert captured["max_tokens"] == get_settings().llm_max_tokens

    def test_a_cut_off_reply_is_reported_as_cut_off(self, monkeypatch):
        # No closing brace — exactly what a reply looks like when max_tokens is
        # hit mid-object.
        truncated = '{"reply": "Right — porridge is the one that needs cov'
        monkeypatch.setattr(
            httpx, "post", lambda *a, **k: _response(_reply(truncated, finish_reason="length"))
        )
        with pytest.raises(llm.LLMError, match="cut short"):
            llm.complete_json("sys", "user")

    def test_a_malformed_reply_that_was_not_cut_off_gets_the_other_message(self, monkeypatch):
        # Same broken JSON, but finish_reason says the model chose to stop —
        # this is a genuinely malformed reply, not truncation, and the log
        # should say so rather than blaming the token limit.
        monkeypatch.setattr(
            httpx, "post", lambda *a, **k: _response(_reply("not json at all", "stop"))
        )
        with pytest.raises(llm.LLMError, match="did not return valid JSON"):
            llm.complete_json("sys", "user")

    def test_a_complete_reply_that_skipped_the_wrapper_carries_its_text(self, monkeypatch):
        # finish_reason "stop" with no JSON in sight: the model answered in
        # full, it just ignored response_format. Distinct from a cut-off
        # reply, and callers with a free-text field can still use it.
        monkeypatch.setattr(
            httpx,
            "post",
            lambda *a, **k: _response(_reply("Right, let's talk about porridge.", "stop")),
        )
        with pytest.raises(llm.LLMNotJSON) as exc_info:
            llm.complete_json("sys", "user")
        assert exc_info.value.content == "Right, let's talk about porridge."


class TestParsing:
    def test_a_complete_reply_parses(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "post", lambda *a, **k: _response(_reply('{"reply": "hi", "check": null}'))
        )
        assert llm.complete_json("sys", "user") == {"reply": "hi", "check": None}

    def test_a_fenced_reply_still_parses(self, monkeypatch):
        fenced = '```json\n{"reply": "hi"}\n```'
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _response(_reply(fenced)))
        assert llm.complete_json("sys", "user") == {"reply": "hi"}

    def test_trailing_commentary_after_the_object_is_dropped(self, monkeypatch):
        chatty = '{"reply": "hi"}\n\nwait, let me reconsider...'
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _response(_reply(chatty)))
        assert llm.complete_json("sys", "user") == {"reply": "hi"}


class TestCleanModelText:
    """Model output as prose, for the screens that render it verbatim.

    Written after a tutor turn reached the reader with
    `<budget:token_budget>999481</budget:token_budget>` on the end of it and a
    literal `*just*` in the middle: provider scaffolding that leaked into the
    generated string, and markdown emphasis in a page that renders plain text.
    """

    def test_leaked_provider_scaffolding_is_removed(self):
        text = "What about the broccoli?\n\n<budget:token_budget>999481</budget:token_budget>"
        assert llm.clean_model_text(text) == "What about the broccoli?"

    def test_a_scaffolding_tag_with_no_body_goes_too(self):
        assert llm.clean_model_text("Fine.<system:done/>") == "Fine."

    def test_ordinary_angle_brackets_are_left_alone(self):
        # Narrow on purpose: a namespaced tag, never a comparison or a face.
        for text in ("glucose < 4 mmol/L", "a < b and b > c", "<3"):
            assert llm.clean_model_text(text) == text

    def test_emphasis_markers_are_unwrapped_not_deleted(self):
        assert llm.clean_model_text("you said *just* the potato") == "you said just the potato"
        assert llm.clean_model_text("that is **the** point") == "that is the point"

    def test_a_lone_asterisk_survives(self):
        # Nothing to unwrap, and eating it would mangle the sentence.
        assert llm.clean_model_text("2 * 3 is six") == "2 * 3 is six"

    def test_paragraphs_survive_but_gaps_do_not_grow(self):
        text = "One.\n\nTwo.\n\n\n\n<budget:x>1</budget:x>\n\nThree."
        assert llm.clean_model_text(text) == "One.\n\nTwo.\n\nThree."

    def test_it_strips_the_way_it_used_to(self):
        assert llm.clean_model_text("  spaced out \n") == "spaced out"
