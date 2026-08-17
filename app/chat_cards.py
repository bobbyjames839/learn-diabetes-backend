"""The cards a chat session leaves behind.

At the end of a conversation the model reads back over it and writes up to five
cards for the things the learner visibly struggled with. This is the one place
in the app where generated text enters the review deck — every other card
quotes authored lesson content, a question's own prompt or a lesson's own key
takeaway — so it is validated harder than it is generated.

Why cards and not a score: the conversation itself is thrown away, and what was
hard about it would go with it. A card is the smallest thing that carries the
struggle forward into a form the learner meets again.

The gap this fills is real. A reader can only get a `weak_spot` card for a
checkpoint that exists, in a lesson they finished; the thing they spent twenty
minutes talking through because no lesson covered it properly leaves no trace
at all. These are the cards for that.

Prompt and validation here, HTTP in llm.py — same split as the other generators.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.chat import Turn
from app.llm import clean_model_text, complete_json
# The shared dosing backstop. A card and a check question are the two things a
# model writes straight to the reader, and both go through it — see app/safety.py.
from app.safety import looks_like_dosing

# Five is the ceiling on what one conversation may add. A session that produced
# more than five genuinely new confusions did not; it produced a model padding.
MAX_CARDS = 5


SYSTEM_PROMPT = """\
A conversation with someone learning to manage type 1 diabetes has just ended.
You are reading it back to write flashcards for their review deck.

Write a card ONLY where the transcript shows they genuinely struggled: they
asked the same thing twice in different words, they said something that revealed
a wrong model of the mechanism, they said "oh — I thought it was the other way
round", or you had to explain something two different ways before it landed.

Write NO cards — return an empty list — when the conversation was short, was
small talk, was them asking a factual question and understanding the answer
first time, or covered nothing they had trouble with. Most short conversations
produce zero or one card. An empty list is a good outcome and a common one.

Hard safety rules — these override everything else:
- Never write a card about choosing an insulin dose, correction factor, carb
  ratio, basal rate, or any specific treatment action. Not on the front, not on
  the back. A card that answers "how much should I take" is a dosing suggestion
  with a flashcard's face on.
- Cards explain MECHANISM only: why glucose behaves the way it does.
- Any numbers are illustrative and belong to a hypothetical person.
- Never write a card that tells them what to do about their own diabetes.

How to write them:
- "front" is a question in their own terms — the thing they were actually
  confused about, phrased as a question they would recognise as theirs.
  Under 200 characters.
- "back" is the answer, plainly: the mechanism, in two or three sentences.
  Under 500 characters. It has to stand alone, months later, with the
  conversation long forgotten. Never write "as we discussed" or "remember when
  you asked" — there is no conversation to point back to.
- "topic" is two or three words naming the area, lowercase, e.g.
  "exercise lows" or "insulin onset".
- Never write a card for something they already understood. A deck padded with
  things they know teaches them to skim it.

Return JSON of exactly this shape and nothing else:
{"cards": [{"front": "...", "back": "...", "topic": "..."}]}
or
{"cards": []}
"""


class GeneratedCard(BaseModel):
    front: str = Field(min_length=1, max_length=200)
    back: str = Field(min_length=1, max_length=500)
    topic: str = Field(default="", max_length=80)

    @field_validator("front", "back", "topic")
    @classmethod
    def _strip(cls, value: str) -> str:
        # More than whitespace: a card is rendered as plain text, so leaked
        # provider scaffolding and markdown asterisks come off here too.
        return clean_model_text(value)


class GeneratedCardSet(BaseModel):
    # Validated one card at a time (see `parse_cards`) rather than as a typed
    # list, so a single over-long card doesn't discard the good ones alongside it.
    cards: list[dict] = Field(default_factory=list)


def build_user_prompt(transcript: list[Turn], existing_fronts: list[str] = ()) -> str:
    """The conversation, laid out so the two speakers are unambiguous.

    `existing_fronts` are this reader's cards from earlier sessions — a card
    authored once in a conversation is kept forever (see module docstring), so
    a recurring confusion needs telling apart from one already on record rather
    than written again in slightly different words.
    """
    parts = ["--- THE CONVERSATION ---"]
    for turn in transcript:
        speaker = "LEARNER" if turn.role == "user" else "YOU"
        parts.append(f"{speaker}: {turn.content}")
    parts.append("")
    if existing_fronts:
        parts.append("--- CARDS THEY ALREADY HAVE, FROM EARLIER SESSIONS ---")
        parts.extend(f"- {front}" for front in existing_fronts)
        parts.append("")
        parts.append(
            "Do not write a card that repeats one of those, even in different words. "
            "Only write one if this conversation showed a genuinely different confusion."
        )
    parts.append(
        f"Write at most {MAX_CARDS} cards, only for things they genuinely "
        "struggled with. Return an empty list if there were none."
    )
    return "\n".join(parts)


def parse_cards(payload: dict, existing_fronts: list[str] = ()) -> list[GeneratedCard]:
    """Validate a model reply into cards fit to store.

    Everything here degrades to fewer cards rather than an error: an unusable
    reply means the session leaves no cards behind, which is the same outcome
    as a conversation that produced none, and no reader ever needs to be told.

    `existing_fronts` backstops the prompt's own instruction not to repeat a
    card the reader already has: the model is asked nicely, but a chat_gap
    card is never revisited once written, so a near-identical one slipping
    through would sit in the deck as a duplicate forever.
    """
    try:
        parsed = GeneratedCardSet.model_validate(payload)
    except ValidationError:
        return []

    kept: list[GeneratedCard] = []
    seen: set[str] = {front.strip().lower() for front in existing_fronts}
    for raw in parsed.cards:
        try:
            card = GeneratedCard.model_validate(raw)
        except ValidationError:
            # One malformed card costs one card, not the session's whole set.
            continue
        if not card.front or not card.back:
            continue
        if looks_like_dosing(card.front) or looks_like_dosing(card.back):
            continue
        # One conversation circling the same confusion shouldn't spend three
        # of the five seats on it — and a card already on record from an
        # earlier session shouldn't be written a second time either.
        key = card.front.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(card)

    return kept[:MAX_CARDS]


def generate(
    transcript: list[Turn], *, model: str | None = None, existing_fronts: list[str] = ()
) -> list[GeneratedCard]:
    """The cards this conversation earned, possibly none. Raises LLMError."""
    if not transcript:
        return []
    payload = complete_json(
        SYSTEM_PROMPT, build_user_prompt(transcript, existing_fronts), model=model
    )
    return parse_cards(payload, existing_fronts)
