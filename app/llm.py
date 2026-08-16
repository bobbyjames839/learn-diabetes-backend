"""Minimal OpenRouter client.

One function, one job: send a prompt, get a JSON object back. Everything that
decides *what* to ask lives in question_gen.py so it can be tested without a
network.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
# Models sometimes wrap JSON in a markdown fence despite being told not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
# First brace to last, for replies with commentary trailing the JSON.
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(RuntimeError):
    """The model was unreachable, too slow, or returned something unusable."""


class LLMNotConfigured(LLMError):
    """No API key. Distinct from a failure, because it is a setup step."""


class LLMNotJSON(LLMError):
    """The model answered in full, but skipped the JSON wrapper.

    Distinct from a truncated reply: `finish_reason` was "stop", not "length"
    — the model just ignored `response_format` and replied in prose, which
    Claude models do occasionally on a long, conversational system prompt. The
    raw text survives on `.content` because a caller with a free-text field
    (the chat reply) can often still use it — the wrapper is a mechanism, not
    the answer someone was waiting for.
    """

    def __init__(self, message: str, content: str) -> None:
        super().__init__(message)
        self.content = content


# Scaffolding a provider occasionally leaves inside the text it generated —
# namespaced pseudo-tags like <budget:token_budget>999481</budget:token_budget>.
# It is not the model's answer and it is not addressed to the reader, but it
# lands inside a string field and every screen renders it verbatim. Deliberately
# narrow: a namespaced tag (`ns:name`), never a bare `<b>` or `<3`.
_SCAFFOLD_TAG = re.compile(r"<(\w+):(\w+)>.*?</\1:\2>|</?\w+:\w+/?>", re.DOTALL)
# Emphasis markers in text that is rendered as plain text, where they show up
# as literal asterisks around the word the model wanted to stress.
_EMPHASIS = re.compile(r"(?<!\w)(\*{1,3})(\S(?:.*?\S)?)\1(?!\w)", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_model_text(text: str) -> str:
    """Model output as prose, for the screens that render it as plain text.

    Two things the reader should never see: provider scaffolding that leaked
    into a generated string, and markdown emphasis in text nothing will parse
    as markdown. Neither is worth failing a reply over — the sentence around
    them is fine — so this cleans rather than rejects.
    """
    cleaned = _SCAFFOLD_TAG.sub("", text)
    cleaned = _EMPHASIS.sub(r"\2", cleaned)
    return _BLANK_RUN.sub("\n\n", cleaned).strip()


def is_configured() -> bool:
    return bool(get_settings().openrouter_api_key)


def complete_json(
    system: str, user: str, *, model: str | None = None, temperature: float = 0.3
) -> dict:
    """Run one completion and parse the reply as a JSON object.

    The default temperature keeps generated questions close to the lesson text
    rather than inventive.
    """
    return complete_json_chat(
        system, [{"role": "user", "content": user}], model=model, temperature=temperature
    )


def complete_json_chat(
    system: str,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.3,
) -> dict:
    """The same, over a multi-turn exchange.

    `messages` is the conversation so far in OpenRouter's own shape — alternating
    `user` and `assistant` turns, without the system prompt, which is prepended
    here. A chat session needs this; everything else in the app is a single
    question and uses `complete_json`.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise LLMNotConfigured("OPENROUTER_API_KEY is not set.")

    payload = {
        "model": model or settings.question_model,
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        # Without this the provider's own default applies, which can be small
        # enough to cut a reply off mid-object — the JSON then has no closing
        # brace and both fallback parsers below correctly refuse to guess at it.
        "max_tokens": settings.llm_max_tokens,
    }

    try:
        response = httpx.post(
            _ENDPOINT,
            json=payload,
            timeout=settings.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException as exc:
        raise LLMError("The model took too long to respond.") from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        log.warning("openrouter returned %s: %s", exc.response.status_code, detail)
        raise LLMError(f"The model provider returned {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise LLMError("Could not reach the model provider.") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError("The model returned an unexpected response shape.") from exc

    return parse_json_content(content, body.get("choices", [{}])[0].get("finish_reason"))


def parse_json_content(content: str, finish_reason: str | None = None) -> dict:
    """The model's raw text, as the JSON object it was asked for.

    Split out from the call itself because the streaming path assembles the same
    text a chunk at a time and needs the identical forgiveness at the end of it:
    the fences, the trailing commentary, and the distinction between a reply that
    was cut off and one that simply ignored the wrapper.
    """
    stripped = _FENCE.sub("", content)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Models sometimes keep talking after the JSON — a closing fence followed by
    # "wait, let me reconsider...". The fence pattern only trims the ends, so
    # fall back to the widest brace-to-brace span in the reply.
    span = _OBJECT.search(stripped)
    if span:
        try:
            return json.loads(span.group(0))
        except json.JSONDecodeError:
            pass

    if finish_reason == "length":
        log.warning(
            "model reply hit max_tokens (%s) and was cut off: %s",
            get_settings().llm_max_tokens,
            content[:300],
        )
        raise LLMError("The model's reply was cut short.")

    log.warning(
        "model did not return JSON (finish_reason=%s): %s", finish_reason, content[:300]
    )
    raise LLMNotJSON("The model did not return valid JSON.", content)


def stream_json_chat(
    system: str,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.3,
) -> Iterator[str]:
    """`complete_json_chat`, one chunk at a time.

    Yields the raw text as the provider sends it — still JSON, still unparsed.
    Assembling it is the caller's job, because the caller is the only thing that
    knows the object's shape and which field is the part worth showing early.

    The same call as the blocking version with `stream: true` added, so a failure
    reads the same way: unreachable, too slow, and refused all raise `LLMError`,
    and a reply cut off by `max_tokens` raises at the end of the stream rather
    than being handed back half-parsed.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise LLMNotConfigured("OPENROUTER_API_KEY is not set.")

    payload = {
        "model": model or settings.question_model,
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": settings.llm_max_tokens,
        "stream": True,
    }

    finish_reason: str | None = None
    try:
        with httpx.stream(
            "POST",
            _ENDPOINT,
            json=payload,
            timeout=settings.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except json.JSONDecodeError:
                    # Providers send comment and keep-alive lines through the
                    # same channel. Not our JSON, not an error.
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = (choice.get("delta") or {}).get("content")
                if delta:
                    yield delta
    except httpx.TimeoutException as exc:
        raise LLMError("The model took too long to respond.") from exc
    except httpx.HTTPStatusError as exc:
        log.warning("openrouter returned %s while streaming", exc.response.status_code)
        raise LLMError(f"The model provider returned {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise LLMError("Could not reach the model provider.") from exc

    if finish_reason == "length":
        log.warning("streamed reply hit max_tokens (%s)", settings.llm_max_tokens)
        raise LLMError("The model's reply was cut short.")
