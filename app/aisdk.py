"""The AI SDK data stream protocol, written out from Python.

The tutor is the one screen where the reader waits on the model with nothing to
read, so it is the one place streaming is worth the machinery. The Vercel AI SDK
solves the client half of that well — `useChat` handles the transport, the
in-flight message, the abort, the error — and since AI SDK 3.4 its wire format
is a documented protocol rather than a TypeScript detail, so a FastAPI backend
can speak it directly. That is what this module does: it is the format, and
nothing else.

Two halves, both pure:

- `sse` and the `*_part` helpers encode the protocol. Server-Sent Events, one
  JSON object per line, closed by a literal `[DONE]`.
- `partial_reply` pulls the `reply` string out of a JSON object that is still
  arriving, which is the awkward part. The tutor's model reply is not plain
  text — it is an object whose `reply` field is the prose and whose other fields
  are the check, the wrap-up proposal, and the profile revision. Streaming the
  raw JSON to the reader would show them the braces, so the text is dug out of
  the buffer as it grows and only the new characters are sent.

Everything the reply carries besides prose is emitted as a `data-*` part once
the object is complete and `chat.parse_reply` has validated it — the attachments
were never streamable anyway, since none of them means anything until the JSON
closes.
"""

from __future__ import annotations

import json
import re

# `"reply"` and its opening quote. The value starts at the end of this match.
_REPLY_KEY = re.compile(r'"reply"\s*:\s*"')
# The short escapes, as JSON defines them. `\u` is handled separately because it
# carries four more characters that may not have arrived yet.
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

STREAM_HEADER = {"x-vercel-ai-ui-message-stream": "v1"}


def sse(part: dict) -> str:
    """One protocol part, as an SSE line."""
    return f"data: {json.dumps(part, separators=(',', ':'))}\n\n"


def done() -> str:
    """The literal terminator the protocol ends on — not JSON, deliberately."""
    return "data: [DONE]\n\n"


def partial_reply(buffer: str) -> str:
    """The `reply` field's text so far, out of a JSON object still arriving.

    Returns "" until the key and its opening quote have both landed, then grows
    with the buffer. Escapes are decoded as they complete: a `\\` or a half-typed
    `\\uxxxx` at the very end of the buffer stops the scan rather than being
    emitted raw, so the caller sees those characters on the next chunk instead of
    seeing a backslash it will never take back.

    Deliberately not a JSON parser. It reads one string value and stops at the
    closing quote; whatever follows in the object is the complete parse's job.
    """
    found = _REPLY_KEY.search(buffer)
    if not found:
        return ""

    out: list[str] = []
    i = found.end()
    while i < len(buffer):
        char = buffer[i]
        if char == '"':
            break
        if char != "\\":
            out.append(char)
            i += 1
            continue

        # An escape. Both branches below stop rather than guess when the escape
        # runs off the end of what has arrived.
        if i + 1 >= len(buffer):
            break
        marker = buffer[i + 1]
        if marker == "u":
            if i + 6 > len(buffer):
                break
            try:
                out.append(json.loads(f'"{buffer[i : i + 6]}"'))
            except json.JSONDecodeError:
                # Not a real code point. Drop it rather than fail the stream —
                # the reader is mid-sentence and this is one character.
                pass
            i += 6
            continue
        out.append(_ESCAPES.get(marker, marker))
        i += 2

    return "".join(out)


def text_stream(message_id: str, deltas) -> "list[str]":
    """A whole text block as protocol parts: start, the deltas, end.

    Takes an iterable so the caller can hand it a generator; returns the parts
    rather than writing them, because this module never touches the wire.
    """
    parts = [sse({"type": "text-start", "id": message_id})]
    parts += [sse({"type": "text-delta", "id": message_id, "delta": d}) for d in deltas]
    parts.append(sse({"type": "text-end", "id": message_id}))
    return parts


def data_part(name: str, payload) -> str:
    """A `data-*` part — the protocol's channel for anything that isn't prose.

    The tutor's check, its wrap-up proposal and its profile revision all come
    through here. `useChat` surfaces them on the message as parts of type
    `data-<name>`, which is how the stepped UI gets them back.
    """
    return sse({"type": f"data-{name}", "data": payload})
