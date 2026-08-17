"""The recap a chat session leaves behind.

The transcript itself is still never stored — see `app/chat.py`. What this
module produces is a short, generated *account* of the session: a headline and
a couple of sentences on what was covered and roughly how it went. That's a
different thing from the conversation, the same way a lesson's key takeaways
are a different thing from its body text, and it is the one artefact a reader
can look back on without the app having kept anything they said.

`POST /api/chat/end` writes one of these alongside the cards it already
writes, and `GET /api/chat/sessions` lists them back. If generation fails or
the model isn't configured, `fallback()` builds a plain, deterministic recap
from the session's own brief and check tally — a summary always exists, even
though the freehand one is better.

Prompt and validation here, HTTP in llm.py — same split as the other generators.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError, field_validator

from app import mastery
from app.chat import Turn
from app.llm import clean_model_text, complete_json
from app.safety import looks_like_dosing_instruction

# Short, sentence-ready phrasing for each topic a reader can pick — distinct
# from `chat.TOPIC_BRIEFS`, which is written to brief the *model*, not to read
# naturally in "a session on ...". `tutor_picks` has no entry: there was no
# named subject, so the fallback falls back further, to "a tutor session".
TOPIC_LABELS: dict[str, str] = {
    "carb_counting": "counting carbs",
    "insulin_action": "how insulin acts",
    "exercise": "exercise",
    "highs_lows": "highs and lows",
    "ketones_sick_days": "ketones and illness",
}


SYSTEM_PROMPT = """\
A conversation with someone learning to manage type 1 diabetes has just ended.
You are reading it back to write a short recap for their own records — the
conversation itself is thrown away, and this is what survives it.

Hard safety rules — these override everything else:
- Never mention or imply a specific insulin dose, correction factor, carb
  ratio, basal rate, or treatment action, even in passing.
- Describe what was covered and how the session went as a piece of learning,
  never as advice about their own diabetes management.

How to write it:
- "headline": three to eight words naming what the session was actually
  about, in plain terms — "Carb counting with mixed meals", not "Session
  summary". No trailing punctuation.
- "summary": two to three sentences. Say what was worked through, then a
  plain, honest sense of how it went — where it clicked, where it took a
  couple of tries. Never a score, a percentage, or a tally; that is tracked
  separately. Never "as we discussed" or anything that assumes the reader
  still has the conversation in front of them — this has to stand alone.
- Write to the reader directly ("you"), warm and plain, like a short note
  rather than a report.

- "area": which part of diabetes management this session was actually about,
  chosen from exactly this list and nothing else:
  - glucose_basics: what makes blood glucose rise and fall
  - carb_counting: counting carbohydrate
  - insulin_action: how insulin acts over time
  - exercise: exercise and activity
  - highs_lows: highs, lows and how they resolve
  - ketones_sick_days: ketones and illness
  - daily_life: everyday life — travel, sleep, stress, alcohol
  Pick the one the conversation spent most of its time on. Use null if it
  genuinely spanned several or none of them — a wrong guess here is worse than
  no answer, because it is recorded as evidence about the reader.

Return JSON of exactly this shape and nothing else:
{"headline": "...", "summary": "...", "area": "carb_counting"}
"""


class GeneratedSummary(BaseModel):
    headline: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    # What the session turned out to be about, from the closed set in
    # `app/mastery.py`. The reader's brief can't answer this on its own: the
    # default topic is `tutor_picks`, where the whole point is that the tutor
    # chose the subject. Null is a legitimate answer, and means the session
    # moves no rating.
    area: str | None = None

    @field_validator("headline", "summary")
    @classmethod
    def _strip(cls, value: str) -> str:
        return clean_model_text(value)

    @field_validator("area", mode="before")
    @classmethod
    def _known_area(cls, value: object) -> str | None:
        # Anything outside the closed set is dropped rather than rejected: a
        # recap with an invented area is still a good recap.
        return value if value in mastery.AREAS else None


def build_user_prompt(transcript: list[Turn], topic_label: str) -> str:
    parts = [f"SESSION SUBJECT: {topic_label}", "", "--- THE CONVERSATION ---"]
    for turn in transcript:
        speaker = "LEARNER" if turn.role == "user" else "YOU"
        parts.append(f"{speaker}: {turn.content}")
    parts.append("")
    parts.append("Write the headline and summary for this session now.")
    return "\n".join(parts)


def parse_summary(payload: dict) -> GeneratedSummary | None:
    """Validate a model reply. `None` means unusable — the caller falls back."""
    try:
        parsed = GeneratedSummary.model_validate(payload)
    except ValidationError:
        return None
    if looks_like_dosing_instruction(parsed.headline) or looks_like_dosing_instruction(parsed.summary):
        return None
    return parsed


def topic_label(topic: str) -> str:
    return TOPIC_LABELS.get(topic, "a tutor session")


def fallback(topic: str, checks_correct: int, checks_total: int) -> GeneratedSummary:
    """A plain, deterministic recap for when generation isn't available.

    No model involved, so this can never fail — a reader who ends a session
    always sees something, even on the day the model call itself doesn't work.
    """
    label = topic_label(topic)
    headline = label[:1].upper() + label[1:] if label != "a tutor session" else "Tutor session"
    if checks_total:
        detail = f" {checks_correct} of {checks_total} checks answered correctly."
    else:
        detail = ""
    return GeneratedSummary(
        headline=headline,
        summary=f"A session on {label}.{detail}",
        # No model read this conversation, so the only attribution available is
        # the subject the reader asked for — and `tutor_picks` doesn't name one.
        area=mastery.TOPIC_AREAS.get(topic),
    )


def generate(
    transcript: list[Turn], *, topic: str, model: str | None = None
) -> GeneratedSummary | None:
    """The recap this conversation earned, or `None` if the reply was unusable.

    Raises LLMError on a network failure — the caller decides whether to fall
    back to `fallback()` (see `routers/chat.py`), the same way a card write
    failing costs nothing worth reporting.
    """
    if not transcript:
        return None
    payload = complete_json(
        SYSTEM_PROMPT, build_user_prompt(transcript, topic_label(topic)), model=model
    )
    return parse_summary(payload)
