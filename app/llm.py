"""Minimal OpenRouter client.

One function, one job: send a prompt, get a JSON object back. Everything that
decides *what* to ask lives in question_gen.py so it can be tested without a
network.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
# Models sometimes wrap JSON in a markdown fence despite being told not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class LLMError(RuntimeError):
    """The model was unreachable, too slow, or returned something unusable."""


class LLMNotConfigured(LLMError):
    """No API key. Distinct from a failure, because it is a setup step."""


def is_configured() -> bool:
    return bool(get_settings().openrouter_api_key)


def complete_json(system: str, user: str, *, model: str | None = None) -> dict:
    """Run one completion and parse the reply as a JSON object."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise LLMNotConfigured("OPENROUTER_API_KEY is not set.")

    payload = {
        "model": model or settings.question_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # Keeps the questions close to the lesson text rather than inventive.
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
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

    try:
        return json.loads(_FENCE.sub("", content))
    except json.JSONDecodeError as exc:
        log.warning("model did not return JSON: %s", content[:300])
        raise LLMError("The model did not return valid JSON.") from exc
