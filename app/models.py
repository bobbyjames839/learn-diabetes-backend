from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Native Postgres uuid, handed to Python as plain strings so the rest of the app
# (JSON payloads, JWT `sub` claims) doesn't have to care. profiles.id must be a
# real uuid because it references auth.users.id.
UuidPk = Uuid(as_uuid=False)


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Profile(Base):
    """One row per Supabase auth user. `id` mirrors auth.users.id."""

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(120))
    diagnosed_year: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # One-time onboarding quiz — why they're here, how they like to learn.
    # Set together (see routers/profile.py), so `onboarding_completed_at` alone
    # tells the frontend whether to show it: NULL means it hasn't run yet.
    onboarding_goal: Mapped[str | None] = mapped_column(String(40))
    onboarding_experience: Mapped[str | None] = mapped_column(String(40))
    onboarding_learning_style: Mapped[str | None] = mapped_column(String(40))
    onboarding_content_preference: Mapped[str | None] = mapped_column(String(40))
    onboarding_focus: Mapped[str | None] = mapped_column(String(40))
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Where this reader stands, area by area, 1-100. The half of the profile
    # that isn't self-reported: lesson sessions and chat sessions move these
    # numbers, and every prompt handed the profile is handed them too. Stored
    # as the whole map; missing or unknown areas fall back to the default in
    # `app/mastery.py`, so the closed set can change without a migration.
    area_ratings: Mapped[dict] = mapped_column(JSON, default=dict)

    progress: Mapped[list[LessonProgress]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class Lesson(Base):
    """Authored lesson content. Seeded from SQL — the app never writes here."""

    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60), default="general", index=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=1)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=5)
    body: Mapped[str] = mapped_column(Text, default="")  # markdown
    key_takeaways: Mapped[list] = mapped_column(JSON, default=list)
    order_index: Mapped[int] = mapped_column(Integer, default=0, index=True)
    published: Mapped[bool] = mapped_column(Boolean, default=True)

    progress: Mapped[list[LessonProgress]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    questions: Mapped[list[LessonQuestion]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan", order_by="LessonQuestion.section_index"
    )


class LessonProgress(Base):
    __tablename__ = "lesson_progress"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_user_lesson"),)

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("lessons.id", ondelete="CASCADE"), index=True
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[Profile] = relationship(back_populates="progress")
    lesson: Mapped[Lesson] = relationship(back_populates="progress")


class LessonQuestion(Base):
    """A checkpoint the reader meets after one section of a lesson.

    Generated once per lesson and kept, rather than regenerated per reader: the
    reading flow can't stall on a model call, and identical questions make the
    responses comparable across users.
    """

    __tablename__ = "lesson_questions"
    __table_args__ = (
        # One checkpoint per section. Also the concurrency guard — two readers
        # opening a fresh lesson at once can't both write a set.
        UniqueConstraint("lesson_id", "section_index", name="uq_lesson_section_question"),
    )

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    lesson_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("lessons.id", ondelete="CASCADE"), index=True
    )
    section_index: Mapped[int] = mapped_column(Integer)
    # Short tag for the idea under test, so responses can be rolled up across
    # lessons that teach the same mechanism.
    concept: Mapped[str] = mapped_column(String(80), default="", index=True)
    prompt: Mapped[str] = mapped_column(Text)
    # [{"text": str, "correct": bool, "misconception": str | None}] — never sent
    # to the client as-is, or the answer would be in the payload.
    options: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lesson: Mapped[Lesson] = relationship(back_populates="questions")
    responses: Mapped[list[QuestionResponse]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class QuestionResponse(Base):
    """One answer by one reader.

    Every attempt is kept, not just the latest — a reader who gets it wrong and
    then right has told us something a single final score would erase.
    """

    __tablename__ = "question_responses"

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("lesson_questions.id", ondelete="CASCADE"), index=True
    )
    chosen_index: Mapped[int] = mapped_column(Integer)
    correct: Mapped[bool] = mapped_column(Boolean, default=False)
    # Copied from the chosen option at answer time. Denormalised on purpose: it
    # is the observation we care about, and it must survive the question being
    # regenerated with different wording.
    misconception: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[LessonQuestion] = relationship(back_populates="responses")


class ChatCard(Base):
    """A card written by the model at the end of a chat session.

    Kept apart from `flashcards` because that deck is a rolling 20 these will
    eventually be pushed out of. Weak spots and takeaways can always be written
    again from `question_responses` and `lessons`; a card authored once in a
    conversation cannot, so it lives here permanently.

    This is the only generated text in the deck — everything else is quoted
    from authored lesson content — so `app/chat_cards.py` validates it against
    the same safety boundary as the lesson and question prompts before it lands.
    """

    __tablename__ = "chat_cards"

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    # What the conversation was about, in the reader's own terms. Shown on the
    # card in place of a lesson title, since a chat belongs to no lesson.
    topic: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ChatSession(Base):
    """The record a chat session leaves behind, in place of the conversation.

    The transcript is still never stored (see `app/chat.py`) — this is a
    generated recap instead: a headline, a couple of sentences on how it went,
    and the check tally the client computed while it was live (a check is
    never recorded elsewhere, so this is the only place that count survives).
    Written once, at `POST /api/chat/end`, alongside the cards from the same
    session; `app/chat_summary.py` writes the headline/summary, falling back
    to a plain deterministic one if generation isn't available.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(String(40), default="tutor_picks")
    headline: Mapped[str] = mapped_column(String(160), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    checks_correct: Mapped[int] = mapped_column(Integer, default=0)
    checks_total: Mapped[int] = mapped_column(Integer, default=0)
    cards_added: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Flashcard(Base):
    """One slot in a reader's 20-slot deck.

    Not derived on read, and not rebuilt either: `app/flashcards.py` treats
    these rows as a queue. A session enqueues what it turned up and the oldest
    slot is overwritten in place to make room, so a row's identity outlives the
    card in it — `position` is the slot number and stays fixed, `updated_at` is
    when this slot last changed hands and is what "oldest" means.
    """

    __tablename__ = "flashcards"
    __table_args__ = (UniqueConstraint("user_id", "position", name="uq_flashcard_position"),)

    id: Mapped[str] = mapped_column(UuidPk, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UuidPk, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    # Set for a weak_spot card, null for a takeaway — nothing reads this back
    # yet, but it's the thread to the source checkpoint if that's ever needed.
    question_id: Mapped[str | None] = mapped_column(
        UuidPk, ForeignKey("lesson_questions.id", ondelete="CASCADE")
    )
    # Null on a chat_gap card: a conversation belongs to no lesson. Both other
    # kinds are quoted from one, and always set it.
    lesson_id: Mapped[str | None] = mapped_column(
        UuidPk, ForeignKey("lessons.id", ondelete="CASCADE")
    )
    # Denormalised alongside front/back, same reasoning as
    # `QuestionResponse.misconception`: this is what the reader saw, and it
    # must survive the lesson or question being edited later.
    lesson_slug: Mapped[str] = mapped_column(String(80), default="")
    lesson_title: Mapped[str] = mapped_column(String(160), default="")
    category: Mapped[str] = mapped_column(String(60), default="")
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
