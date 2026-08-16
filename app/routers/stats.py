from collections import defaultdict
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import (
    ChatCard,
    Flashcard,
    Lesson,
    LessonProgress,
    LessonQuestion,
    Profile,
    QuestionResponse,
    utcnow,
)
from app.routers.lessons import _summary
from app.schemas import CategoryStat, DeckStat, FlashcardOut, StatsOut, TroubleSpotOut
from app.stats import FirstTry, compute_streak, percent, rank_trouble_spots

router = APIRouter()


@router.get("/stats", response_model=StatsOut)
def stats(
    profile: Profile = Depends(get_current_user), db: Session = Depends(get_db)
) -> StatsOut:
    lessons = db.scalars(
        select(Lesson).where(Lesson.published.is_(True)).order_by(Lesson.order_index)
    ).all()
    progress = {
        r.lesson_id: r
        for r in db.scalars(
            select(LessonProgress).where(LessonProgress.user_id == profile.id)
        ).all()
    }

    completed_ids = {lid for lid, r in progress.items() if r.completed}
    minutes = sum(l.estimated_minutes for l in lessons if l.id in completed_ids)

    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for lesson in lessons:
        by_category[lesson.category][0] += 1
        if lesson.id in completed_ids:
            by_category[lesson.category][1] += 1

    completion_days = {
        r.completed_at.astimezone(timezone.utc).date()
        for r in progress.values()
        if r.completed and r.completed_at
    }

    activity = [r.last_viewed_at for r in progress.values() if r.last_viewed_at]

    # Continue with the first unfinished lesson in curriculum order.
    next_lesson = next((l for l in lessons if l.id not in completed_ids), None)

    # Checkpoints, first attempts only. A recorded answer means its lesson was
    # completed, so these only ever count sessions the reader saw through.
    first_tries = [
        FirstTry(slug, title, category, correct)
        for slug, title, category, correct in db.execute(
            select(
                Lesson.slug,
                Lesson.title,
                Lesson.category,
                QuestionResponse.correct,
            )
            .join(LessonQuestion, QuestionResponse.question_id == LessonQuestion.id)
            .join(Lesson, LessonQuestion.lesson_id == Lesson.id)
            .where(QuestionResponse.user_id == profile.id, QuestionResponse.attempt == 1)
        ).all()
    ]
    answered = len(first_tries)
    correct = sum(1 for t in first_tries if t.correct)

    # The deck by where its cards came from. One grouped count rather than
    # pulling twenty rows back to tally them here.
    deck_counts: dict[str, int] = dict(
        db.execute(
            select(Flashcard.kind, func.count())
            .where(Flashcard.user_id == profile.id)
            .group_by(Flashcard.kind)
        ).all()
    )
    deck = DeckStat(
        total=sum(deck_counts.values()),
        weak_spots=deck_counts.get("weak_spot", 0),
        from_tutor=deck_counts.get("chat_gap", 0),
        starters=deck_counts.get("takeaway", 0),
    )

    tutor_cards = db.scalar(
        select(func.count()).select_from(ChatCard).where(ChatCard.user_id == profile.id)
    )

    return StatsOut(
        lessons_total=len(lessons),
        lessons_completed=len(completed_ids),
        percent_complete=percent(len(completed_ids), len(lessons)),
        minutes_learned=minutes,
        streak_days=compute_streak(completion_days, utcnow().date()),
        last_activity_at=max(activity) if activity else None,
        by_category=[
            CategoryStat(category=c, total=t, completed=d)
            for c, (t, d) in sorted(by_category.items())
        ],
        next_lesson=_summary(next_lesson, progress.get(next_lesson.id)) if next_lesson else None,
        checkpoints_answered=answered,
        checkpoints_correct=correct,
        checkpoint_accuracy=percent(correct, answered),
        deck=deck,
        tutor_cards=tutor_cards or 0,
        trouble_spots=[
            TroubleSpotOut(
                lesson_slug=s.lesson_slug,
                lesson_title=s.lesson_title,
                category=s.category,
                asked=s.asked,
                missed=s.missed,
            )
            for s in rank_trouble_spots(first_tries)
        ],
    )


@router.get("/flashcards", response_model=list[FlashcardOut])
def flashcards(
    profile: Profile = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[FlashcardOut]:
    """This reader's stored deck, most recently earned card first.

    A plain read — the deck is a queue that `app/flashcards.py` adds to at
    account creation, at the end of a lesson session and at the end of a chat
    session. Nothing on this path writes.

    Ordered by when each slot was last written rather than by `position`, which
    is only ever a slot number: a card that arrives after a hard session takes
    whichever slot fell due, and the reader should not have to go looking for it
    somewhere in the middle of the deck.
    """
    rows = db.scalars(
        select(Flashcard)
        .where(Flashcard.user_id == profile.id)
        .order_by(Flashcard.updated_at.desc(), Flashcard.position)
    ).all()
    return [
        FlashcardOut(
            id=row.id,
            kind=row.kind,
            lesson_slug=row.lesson_slug,
            lesson_title=row.lesson_title,
            category=row.category,
            front=row.front,
            back=row.back,
        )
        for row in rows
    ]
