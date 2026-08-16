from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import flashcards
from app.auth import get_current_user
from app.db import get_db
from app import mastery
from app.learner import LearnerEvidence, profile_revisions
from app.models import Lesson, LessonProgress, LessonQuestion, Profile, QuestionResponse, utcnow
from app.schemas import (
    AnswerAttemptIn,
    LessonDetail,
    LessonProgressOut,
    LessonSection,
    LessonSummary,
    ProgressUpdate,
)
from app.sections import split_sections

router = APIRouter()


def _progress_map(db: Session, user_id: str) -> dict[str, LessonProgress]:
    rows = db.scalars(
        select(LessonProgress).where(LessonProgress.user_id == user_id)
    ).all()
    return {r.lesson_id: r for r in rows}


def _summary(lesson: Lesson, progress: LessonProgress | None) -> LessonSummary:
    return LessonSummary(
        slug=lesson.slug,
        title=lesson.title,
        summary=lesson.summary,
        category=lesson.category,
        difficulty=lesson.difficulty,
        estimated_minutes=lesson.estimated_minutes,
        order_index=lesson.order_index,
        completed=bool(progress and progress.completed),
        last_viewed_at=progress.last_viewed_at if progress else None,
    )


def _wrong_by_category(db: Session, user_id: str) -> dict[str, int]:
    """How many checkpoints this reader has missed, per lesson category.

    Counts attempts, not distinct questions: missing the same checkpoint three
    times says more about that category than missing three different ones once.
    """
    rows = db.execute(
        select(Lesson.category, func.count())
        .select_from(QuestionResponse)
        .join(LessonQuestion, LessonQuestion.id == QuestionResponse.question_id)
        .join(Lesson, Lesson.id == LessonQuestion.lesson_id)
        .where(QuestionResponse.user_id == user_id, QuestionResponse.correct.is_(False))
        .group_by(Lesson.category)
    ).all()
    return {category: count for category, count in rows}


@router.get("/lessons", response_model=list[LessonSummary])
def list_lessons(
    profile: Profile = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[LessonSummary]:
    lessons = db.scalars(
        select(Lesson).where(Lesson.published.is_(True)).order_by(Lesson.order_index)
    ).all()
    progress = _progress_map(db, profile.id)
    return [_summary(lesson, progress.get(lesson.id)) for lesson in lessons]


@router.get("/lessons/{slug}", response_model=LessonDetail)
def get_lesson(
    slug: str,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LessonDetail:
    lesson = db.scalar(select(Lesson).where(Lesson.slug == slug, Lesson.published.is_(True)))
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")

    # Opening a lesson records a view, which is what "continue where you left off" uses.
    progress = db.scalar(
        select(LessonProgress).where(
            LessonProgress.user_id == profile.id, LessonProgress.lesson_id == lesson.id
        )
    )
    if progress is None:
        progress = LessonProgress(user_id=profile.id, lesson_id=lesson.id)
        db.add(progress)
    else:
        progress.last_viewed_at = utcnow()
    db.commit()

    base = _summary(lesson, progress)
    return LessonDetail(
        **base.model_dump(),
        body=lesson.body,
        key_takeaways=list(lesson.key_takeaways or []),
        # Split server-side so the reader and the checkpoints agree on where one
        # section ends and the next begins.
        sections=[
            LessonSection(index=s.index, heading=s.heading, markdown=s.markdown)
            for s in split_sections(lesson.body)
        ],
    )


@router.post("/lessons/{slug}/progress", response_model=LessonProgressOut)
def set_progress(
    slug: str,
    payload: ProgressUpdate,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LessonProgressOut:
    lesson = db.scalar(select(Lesson).where(Lesson.slug == slug, Lesson.published.is_(True)))
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")

    progress = db.scalar(
        select(LessonProgress).where(
            LessonProgress.user_id == profile.id, LessonProgress.lesson_id == lesson.id
        )
    )
    if progress is None:
        progress = LessonProgress(user_id=profile.id, lesson_id=lesson.id)
        db.add(progress)

    progress.completed = payload.completed
    progress.last_viewed_at = utcnow()
    # Keep the original completion date so the streak isn't re-dated by a revisit.
    if payload.completed and progress.completed_at is None:
        progress.completed_at = utcnow()
    elif not payload.completed:
        progress.completed_at = None

    # Checkpoint attempts only become a permanent record here, alongside
    # completion. A reader who never reaches this button leaves nothing behind.
    first_tries = (0, 0)
    if payload.completed and payload.answers:
        first_tries = _record_answers(db, profile.id, lesson.id, payload.answers)

    if payload.completed:
        # The three writes a lesson session makes, in the order they depend on
        # each other and inside one transaction: the answers above, then the
        # profile, then whatever this session earned a place in the deck.
        #
        # The deck is added to, not rebuilt — the checkpoints missed here and
        # this lesson's takeaways go in, and the oldest cards drop off to make
        # room. Everything the reader earned in earlier sessions stays put.
        db.flush()
        _revise_profile(db, profile)
        _rate_area(profile, lesson, first_tries)
        flashcards.enqueue(db, profile.id, flashcards.lesson_candidates(db, profile.id, lesson))

    db.commit()

    return LessonProgressOut(
        slug=lesson.slug,
        completed=progress.completed,
        last_viewed_at=progress.last_viewed_at,
        completed_at=progress.completed_at,
    )


def _revise_profile(db: Session, profile: Profile) -> None:
    """Update what the profile claims about this reader, if the numbers moved.

    Runs after the session's answers are flushed, so the counts include the
    lesson just finished. Deterministic — the ranking lives in `app/learner.py`
    and this only does the counting. Usually a no-op: finishing one more lesson
    rarely changes what we can say about someone.
    """
    asked, correct = db.execute(
        select(
            func.count(),
            func.count().filter(QuestionResponse.correct.is_(True)),
        ).where(QuestionResponse.user_id == profile.id)
    ).one()

    completed = (
        db.scalar(
            select(func.count())
            .select_from(LessonProgress)
            .where(
                LessonProgress.user_id == profile.id,
                LessonProgress.completed.is_(True),
            )
        )
        or 0
    )

    revisions = profile_revisions(
        profile.onboarding_experience,
        profile.onboarding_focus,
        LearnerEvidence(
            lessons_completed=completed,
            questions_asked=asked or 0,
            questions_correct=correct or 0,
            wrong_by_category=_wrong_by_category(db, profile.id),
        ),
    )
    for field, value in revisions.items():
        setattr(profile, field, value)


def _rate_area(profile: Profile, lesson: Lesson, first_tries: tuple[int, int]) -> None:
    """Move this lesson's area rating by how the session's checkpoints went.

    First attempts only, and only the lesson just finished: the rating is a
    running view built one session at a time, so a session contributes what it
    saw rather than a recount of everything. The area comes from the lesson's
    own category — the coarsest attribution that is always right, since a
    lesson belongs to exactly one.
    """
    area = mastery.CATEGORY_AREAS.get(lesson.category)
    correct, total = first_tries
    if area is None or total == 0:
        return
    profile.area_ratings = mastery.apply_evidence(profile.area_ratings, {area: (correct, total)})


def _record_answers(
    db: Session, user_id: str, lesson_id: str, answers: list[AnswerAttemptIn]
) -> tuple[int, int]:
    """Turn one session's checkpoint attempts into `QuestionResponse` rows.

    Correctness and the misconception are read back off the question's own
    answer key, never trusted from the client — the payload only ever carries
    which option was picked, in order.

    Returns `(correct, total)` over *first* attempts, which is what the area
    rating is moved by: the retry that follows coaching is teaching, not
    evidence.
    """
    questions = {
        q.id: q
        for q in db.scalars(
            select(LessonQuestion).where(
                LessonQuestion.lesson_id == lesson_id,
                LessonQuestion.id.in_({a.question_id for a in answers}),
            )
        ).all()
    }
    if not questions:
        return (0, 0)

    # Completing a lesson again after redoing its checkpoints replaces the old
    # observation rather than piling a second attempt sequence on top of it.
    db.execute(
        delete(QuestionResponse).where(
            QuestionResponse.user_id == user_id,
            QuestionResponse.question_id.in_(questions.keys()),
        )
    )

    attempt_counts: dict[str, int] = {}
    first_correct = 0
    first_total = 0
    for answer in answers:
        question = questions.get(answer.question_id)
        if question is None:
            continue
        options = question.options or []
        if answer.chosen_index >= len(options):
            continue

        chosen = options[answer.chosen_index]
        correct = bool(chosen.get("correct"))
        attempt_counts[answer.question_id] = attempt_counts.get(answer.question_id, 0) + 1
        if attempt_counts[answer.question_id] == 1:
            first_total += 1
            first_correct += int(correct)

        db.add(
            QuestionResponse(
                user_id=user_id,
                question_id=answer.question_id,
                chosen_index=answer.chosen_index,
                correct=correct,
                misconception=None if correct else chosen.get("misconception"),
                attempt=attempt_counts[answer.question_id],
            )
        )

    return (first_correct, first_total)
