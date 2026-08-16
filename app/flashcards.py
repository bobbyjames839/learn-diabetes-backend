"""The stored, per-reader flashcard deck.

The deck is a **queue of 20 slots**, not a view and not a snapshot. It is filled
once at account creation and from then on it is only ever *added to*: a session
that turns up something worth reviewing enqueues it, and the deck makes room by
dropping its oldest card. Nothing is recomputed, so a card the reader earned in
March survives every session after it until 20 newer cards have pushed it out.

That is the difference from a rebuild. A rebuild asks "what are this reader's
20 best cards right now?" and answers it from scratch every time, which means
the deck silently reshuffles under someone who did nothing but finish an easy
lesson. A queue asks only "what did this session teach us?" and leaves the rest
of the deck alone.

Three things enqueue, and they are the same three moments the app writes:

- `seed_default` — account creation. Fills the empty deck with the twenty
  hand-written cards in `app/starter_deck.py`, because there is no history to
  draw on yet.
- `lesson_candidates` → `enqueue` — the end of a lesson session: the checkpoints
  they got wrong in it, then that lesson's key takeaways.
- `chat_candidates` → `enqueue` — the end of a chat session: the cards the model
  wrote for what they visibly struggled with.

Reviewing the deck writes nothing. `GET /api/flashcards` is a plain select.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ChatCard,
    Flashcard,
    Lesson,
    LessonQuestion,
    QuestionResponse,
    utcnow,
)
from app.starter_deck import STARTER_DECK

DECK_SIZE = 20

# The fields a slot carries. Everything else on the row — id, user, position —
# belongs to the slot rather than the card in it, and survives an overwrite.
_CARD_FIELDS = (
    "kind",
    "question_id",
    "lesson_id",
    "lesson_slug",
    "lesson_title",
    "category",
    "front",
    "back",
)

# Which kinds give up their slot first. A takeaway is filler — it is the card a
# reader gets when there is nothing sharper to show them — so it yields to a new
# arrival before a weak spot or a chat card does, however recently it landed.
# Within a tier it is strictly oldest-first, which is the queue.
_FILLER = "takeaway"

# Stands in for "written just now" while a plan is being worked out. Never
# persisted — the real timestamp is stamped on by `enqueue`.
_NEWEST = datetime.max.replace(tzinfo=timezone.utc)


def _key(row: dict | Flashcard) -> tuple:
    """What makes two cards the same card.

    `question_id` settles weak spots; the text settles the other two, neither of
    which has an id the deck stores. Used to renew a card already in the deck
    rather than enqueue a second copy of it — a reader who misses the same
    checkpoint in two sessions should have it held in the deck longer, not
    occupying two slots.
    """
    if isinstance(row, Flashcard):
        return (row.kind, row.question_id, row.front, row.back)
    return (row["kind"], row.get("question_id"), row["front"], row["back"])


def _wrong_in_lesson(db: Session, user_id: str, lesson: Lesson) -> list[dict]:
    """Checkpoints from this lesson the reader has just gotten wrong, worst first.

    Scoped to the one lesson on purpose: this is a session's intake, not a
    census. `_record_answers` replaces this reader's responses for these
    questions before we run, so what this reads back is exactly the session that
    just ended.
    """
    rows = db.execute(
        select(
            LessonQuestion.id,
            LessonQuestion.prompt,
            LessonQuestion.explanation,
            QuestionResponse.answered_at,
        )
        .join(LessonQuestion, LessonQuestion.id == QuestionResponse.question_id)
        .where(
            QuestionResponse.user_id == user_id,
            QuestionResponse.correct.is_(False),
            LessonQuestion.lesson_id == lesson.id,
        )
        .order_by(QuestionResponse.answered_at)
    ).all()

    # One card per checkpoint however many times it was missed, but missing it
    # twice still counts — it goes in ahead of one missed once.
    misses: dict[str, int] = {}
    seen: dict[str, dict] = {}
    for question_id, prompt, explanation, _answered_at in rows:
        misses[question_id] = misses.get(question_id, 0) + 1
        seen.setdefault(
            question_id,
            {
                "kind": "weak_spot",
                "question_id": question_id,
                "lesson_id": lesson.id,
                "lesson_slug": lesson.slug,
                "lesson_title": lesson.title,
                "category": lesson.category,
                "front": prompt,
                "back": explanation,
            },
        )
    return sorted(seen.values(), key=lambda c: -misses[c["question_id"]])


def _takeaways(lesson: Lesson) -> list[dict]:
    return [
        {
            "kind": "takeaway",
            "question_id": None,
            "lesson_id": lesson.id,
            "lesson_slug": lesson.slug,
            "lesson_title": lesson.title,
            "category": lesson.category,
            "front": lesson.title,
            "back": takeaway,
        }
        for takeaway in lesson.key_takeaways or []
    ]


def lesson_candidates(db: Session, user_id: str, lesson: Lesson) -> list[dict]:
    """What a finished lesson session offers the deck, best first.

    The checkpoints they missed lead, because that is the whole reason the deck
    exists; the lesson's authored takeaways follow, so a reader who got
    everything right still leaves the session with something. Offering them in
    that order matters — the deck fills from the front, so if the queue can only
    take some of them it takes the weak spots.
    """
    return _wrong_in_lesson(db, user_id, lesson) + _takeaways(lesson)


def chat_candidates(cards: list[ChatCard]) -> list[dict]:
    """What a finished chat session offers the deck.

    Already bounded by `chat_cards.MAX_CARDS`, and already the model's pick of
    what the reader struggled with, so there is no ranking left to do here.

    A chat belongs to no lesson, so the lesson columns are empty and the card
    carries the conversation's topic in the slot a lesson title would use.
    """
    return [
        {
            "kind": "chat_gap",
            "question_id": None,
            "lesson_id": None,
            "lesson_slug": "",
            "lesson_title": card.topic,
            "category": "",
            "front": card.front,
            "back": card.back,
        }
        for card in cards
    ]


@dataclass(frozen=True)
class Slot:
    """One occupied slot, as the planner needs to see it."""

    position: int
    key: tuple
    kind: str
    age: datetime


@dataclass(frozen=True)
class Intake:
    """What one session does to the deck.

    Two lists of slot positions, because those are the only two things that can
    happen to a queue: a card already in the deck has its clock reset, or a new
    card takes a slot. Nothing is ever removed on its own.
    """

    renewed: list[int]
    placed: list[tuple[int, dict]]

    def __len__(self) -> int:
        return len(self.renewed) + len(self.placed)


def plan_intake(deck: list[Slot], candidates: list[dict], size: int = DECK_SIZE) -> Intake:
    """Where a session's cards go. Pure — the decisions, none of the writing.

    For each candidate in the order offered:

    - already in the deck → renew it. A checkpoint the reader misses again
      should be *held* longer, not held twice.
    - deck not full → the lowest free slot.
    - deck full → the oldest slot, with takeaways yielding before weak spots and
      chat cards. Strict age within a tier; that is the queue. The tiering is
      the one exception, and it earns itself: takeaways are the filler a deck
      falls back on, and without it an easy lesson's takeaways would evict the
      hard-won cards from the session before it.

    Never evicts a slot this same call just wrote, which is why intake is capped
    at one deckful — beyond that a session is only overwriting itself.
    """
    slots = {slot.position: slot for slot in deck}
    by_key = {slot.key: slot.position for slot in deck}
    fresh: set[int] = set()
    renewed: list[int] = []
    placed: list[tuple[int, dict]] = []

    for candidate in candidates[:size]:
        key = _key(candidate)
        if key in by_key:
            renewed.append(by_key[key])
            continue

        free = [p for p in range(size) if p not in slots]
        if free:
            position = free[0]
        else:
            # `or slots.values()` can't be reached while intake is capped at one
            # deckful, but it keeps a write path from raising if that ever moves.
            available = [s for s in slots.values() if s.position not in fresh] or list(
                slots.values()
            )
            fillers = [s for s in available if s.kind == _FILLER]
            # `position` only breaks ties — a freshly seeded deck writes all 20
            # slots in one instant and eviction still has to be deterministic.
            evicted = min(fillers or available, key=lambda s: (s.age, s.position))
            position = evicted.position
            del by_key[evicted.key]

        slots[position] = Slot(position=position, key=key, kind=candidate["kind"], age=_NEWEST)
        by_key[key] = position
        fresh.add(position)
        placed.append((position, candidate))

    return Intake(renewed=renewed, placed=placed)


def enqueue(db: Session, user_id: str, candidates: list[dict]) -> int:
    """Add a session's cards to the deck, dropping the oldest to make room.

    Returns how many slots the session touched. Zero is normal — a lesson whose
    takeaways are already in the deck and whose checkpoints were all answered
    correctly has nothing new to say.

    Does not commit: this is one write inside a session's transaction, alongside
    the answers and the profile revision it belongs with.
    """
    rows = {
        row.position: row
        for row in db.scalars(select(Flashcard).where(Flashcard.user_id == user_id)).all()
    }
    intake = plan_intake(
        [
            Slot(position=row.position, key=_key(row), kind=row.kind, age=row.updated_at)
            for row in rows.values()
        ],
        candidates,
    )
    now = utcnow()

    for position in intake.renewed:
        rows[position].updated_at = now

    for position, card in intake.placed:
        row = rows.get(position)
        if row is None:
            db.add(Flashcard(user_id=user_id, position=position, updated_at=now, **card))
            continue
        # Overwritten in place rather than deleted and reinserted: the slot is
        # what persists here, and reusing it keeps the unique (user, position)
        # constraint out of the way.
        for field in _CARD_FIELDS:
            setattr(row, field, card[field])
        row.updated_at = now

    return len(intake)


def starter_rows(db: Session) -> list[dict]:
    """The hand-written starting deck, linked up to the lessons it came from.

    The card text is authored in `app/starter_deck.py` and is not derived from
    anything — see the note there on why the lesson-takeaway version didn't
    work. All this does is resolve each card's lesson so the reader can go and
    read the long version, falling back to the card's own topic and category if
    that lesson isn't in the database.
    """
    lessons = {
        lesson.slug: lesson
        for lesson in db.scalars(
            select(Lesson).where(
                Lesson.slug.in_({card.lesson_slug for card in STARTER_DECK})
            )
        ).all()
    }
    return [
        {
            # Filler, and meant to be: these give up their slots, oldest first,
            # to the checkpoints the reader actually misses.
            "kind": "takeaway",
            "question_id": None,
            "lesson_id": lesson.id if lesson else None,
            "lesson_slug": card.lesson_slug if lesson else "",
            "lesson_title": lesson.title if lesson else card.topic,
            "category": lesson.category if lesson else card.category,
            "front": card.front,
            "back": card.back,
        }
        for card, lesson in ((card, lessons.get(card.lesson_slug)) for card in STARTER_DECK)
    ]


def seed_default(db: Session, user_id: str) -> None:
    """A brand new reader's starting deck: the twenty authored starter cards.

    There is no history to build from on day one, so nothing here is derived.
    Every card is filler, and the reader's own material displaces it as they
    work — after twenty missed checkpoints and tutor cards, none of this is left.
    """
    enqueue(db, user_id, starter_rows(db))
    db.commit()
