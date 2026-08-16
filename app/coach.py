"""The tip that appears alongside the section a reader is on.

One short, plainly useful thing about the idea on screen — a restatement in
different words, a small analogy, the part people usually trip over. It reads as
teaching, not as feedback.

The reader's recent answers are sent to the model, but only so it can judge
which part of the section is worth explaining; the tip itself never mentions
them. Being told "earlier you got this wrong" over and over is a worse
experience than a good tip, and it turns every screen into a report card. The
prompt forbids it explicitly, since a model handed an answer history will reach
for it otherwise.

Unlike the checkpoints, this cannot be a build step: which tip is worth writing
depends on who is reading, so it is generated per screen, per reader. The prompt
and its validation live here, apart from the HTTP call, so both can be tested
without a network — the same split as question_gen.py.

Saying nothing is a valid outcome and an important one. A tip on every single
screen is wallpaper, and readers learn to stop seeing it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from app import mastery
from app.llm import clean_model_text, complete_json

# The same boundary the lesson and question prompts carry. A friendly aside is
# still the app talking to someone with type 1 diabetes, and a remark that
# drifts into "so you'd take a bit more for that" is a dosing suggestion no
# matter how conversational the wrapper.
SYSTEM_PROMPT = """\
You are a warm, plain-spoken diabetes educator. Someone is reading a lesson, and
you write the short tip that appears alongside the section they are on.

Write ONE genuinely useful tip about the idea on this screen. Something that
makes it click: a plain-language restatement of the mechanism, a small everyday
analogy, the thing people usually find confusing about it, or a "the reason this
matters is..." that the text leaves implicit.

Hard safety rules — these override everything else:
- Never suggest, imply, or ask them to choose an insulin dose, correction
  factor, carb ratio, basal rate, or any specific treatment action.
- Explain MECHANISM only: why glucose behaves as it does.
- Any numbers are illustrative and belong to a hypothetical person.
- Never imply they should act on this without their diabetes team.

How to write it:
- ONE sentence, two at the very most. Under 180 characters. Hard limit.
- Plain and concrete. A tip that needs re-reading is not a tip.
- Warm and casual, never patronising. A knowledgeable friend, not a cheerleader.
- Never quote the section's wording back at them — say it a different way, or
  say a different thing. Repeating the paragraph they just read is useless.
- Never mention quizzes, questions, scores, answers, or how they have been
  doing. Do not say "you got this wrong", "earlier you thought", "remember when
  you", or anything of that shape. The tip stands on its own as a piece of
  teaching. This holds even though you are shown their recent answers.
- Never reveal or hint at the answer to a question they have not yet reached.

You are shown their recent answers only so you can judge which part of this
section is worth explaining — lean toward the ideas they have found slippery.
Use it to CHOOSE the tip, never to REFERENCE their performance.

Do not let their history dominate. The tip is about THIS section's own content;
their answers are a tiebreaker for which part of it to draw out, nothing more.
If the section does not genuinely touch an idea in their history, ignore the
history completely. Writing the same point about the same misconception on
screen after screen is worse than saying nothing.

Then rate your own tip honestly with "value", using these anchors. Be strict —
the rating decides whether it is shown at all, and a screen showing no tip is a
perfectly good outcome:
  5 - names a confusion people genuinely hold that this section walks past.
      Without it, a reader could finish the section still holding it.
  4 - draws out a real consequence the section leaves implicit, or makes a
      genuinely hard idea land in one line.
  3 - true and mildly additive, but a reader who missed it has lost nothing.
  2 - mostly a restatement of the section in different words.
  1 - filler, or an analogy for something already explained clearly.

Most tips are a 2 or a 3. A 5 is rare. Do not inflate: if you find yourself
rating everything 4, you are rating how nice the sentence is rather than how
much it teaches. Rate what a reader would LOSE if it were not shown.

Return "message": null when the section is short, introductory, already plain,
or you have nothing to add. Then "value" is 1.

Return JSON of exactly this shape and nothing else:
{"message": "...", "value": 4} or {"message": null, "value": 1}
"""


@dataclass(frozen=True)
class PastAnswer:
    """One thing this reader has already done, flattened for the prompt."""

    concept: str
    question: str
    correct: bool
    # Only set when they got it wrong — the wrong model that answer revealed.
    misconception: str | None = None


class CoachMessage(BaseModel):
    # A tip long enough to need scrolling isn't a tip. The prompt asks for 180;
    # this is the backstop for a model that runs on anyway.
    message: str | None = Field(default=None, max_length=240)
    # The model's own rating of how much the tip teaches, 1-5. Asking for a tip
    # and asking whether it was worth writing are different questions, and a
    # model told to stay quiet "two times in three" cannot comply — each call is
    # independent and it has no view of the distribution. Scoring per call it
    # can do; the threshold then lives here, where it is tunable.
    value: int = Field(default=1, ge=1, le=5)


# Thresholding the model's own score cannot control how often a tip appears: run
# over all 77 sections the scores come back 2x3, 60x4, 15x5, so the cut is
# either 97% or 19% with nothing between. Models rate their own work on a narrow
# band no matter how the anchors are worded.
#
# So the score decides *which* tips are worth showing and a deterministic gate
# decides *how many*. A 5 always shows; a 4 shows for a stable fraction of
# reader-and-section pairs. Together those land near 40%.
#
# The gate is a hash rather than a coin flip so a reader coming back to a
# section sees what they saw last time — a tip that flickers in and out on
# re-read reads as a bug.
ALWAYS_SHOW_AT = 5
MAYBE_SHOW_AT = 4
MAYBE_SHOW_PERCENT = 28


def build_user_prompt(
    lesson_title: str,
    section_heading: str,
    section_body: str,
    history: list[PastAnswer],
    already_shown: list[str] | None = None,
    area_ratings: dict | None = None,
) -> str:
    parts = [
        f"LESSON: {lesson_title}",
        f"SCREEN THEY ARE READING NOW: {section_heading or '(introduction)'}",
        "",
        section_body,
        "",
        "--- BACKGROUND ONLY: their recent answers, most recent first. Use this",
        "--- to pick what to explain. Never refer to it in the tip.",
    ]

    if not history:
        parts.append("(none yet — this is the first thing they have done)")
    else:
        for answer in history:
            verdict = "got it right" if answer.correct else "got it wrong"
            line = f"- [{answer.concept}] {verdict}: {answer.question}"
            if answer.misconception:
                line += f"\n    what that revealed: {answer.misconception}"
            parts.append(line)

    # Where they stand, the same map the tutor is handed. It sets the pitch of
    # the tip — the same idea is worth explaining differently to someone shaky
    # in this area and someone strong in it.
    parts.append("")
    parts.append(mastery.describe(area_ratings))

    if already_shown:
        parts.append("")
        parts.append("--- TIPS YOU HAVE ALREADY GIVEN THEM EARLIER IN THIS LESSON ---")
        parts.extend(f"- {tip}" for tip in already_shown)
        parts.append(
            "Do not make the same point again in different words. If the only "
            'tip you have for this screen is one of these, return null.'
        )

    parts.append("")
    parts.append(
        "Write one short, useful tip about the idea on this screen, or return "
        "null if there is nothing worth adding. Do not mention their answers."
    )
    return "\n".join(parts)


def should_show(value: int, seed: str) -> bool:
    """Whether a tip of this quality earns the interruption for this reader.

    `seed` identifies the reader and the section, so the answer is stable: the
    same person returning to the same section gets the same decision.
    """
    if value >= ALWAYS_SHOW_AT:
        return True
    if value < MAYBE_SHOW_AT:
        return False
    digest = hashlib.sha256(seed.encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < MAYBE_SHOW_PERCENT


def parse_message(payload: dict, seed: str) -> str | None:
    """Validate a model reply, and drop the tips that didn't earn their place.

    An unusable reply is silence, not an error — the reader loses a tip, which
    is a non-event.
    """
    try:
        parsed = CoachMessage.model_validate(payload)
    except ValidationError:
        return None

    if parsed.message is None or not should_show(parsed.value, seed):
        return None
    # Rendered verbatim in the toast, so leaked scaffolding and markdown
    # asterisks are stripped rather than shown. See `llm.clean_model_text`.
    text = clean_model_text(parsed.message)
    return text or None


def generate(
    lesson_title: str,
    section_heading: str,
    section_body: str,
    history: list[PastAnswer],
    *,
    seed: str,
    already_shown: list[str] | None = None,
    area_ratings: dict | None = None,
    model: str | None = None,
) -> str | None:
    """The tip for one screen, or None. Raises LLMError."""
    payload = complete_json(
        SYSTEM_PROMPT,
        build_user_prompt(
            lesson_title, section_heading, section_body, history, already_shown, area_ratings
        ),
        model=model,
    )
    return parse_message(payload, seed)
