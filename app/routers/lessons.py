from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Lesson, LessonProgress, Profile, utcnow
from app.schemas import (
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
    db.commit()

    return LessonProgressOut(
        slug=lesson.slug,
        completed=progress.completed,
        last_viewed_at=progress.last_viewed_at,
        completed_at=progress.completed_at,
    )
