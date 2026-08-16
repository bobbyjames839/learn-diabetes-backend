"""The companion that reads over the reader's shoulder.

This is the one place in the app where a model runs inside a request. The
checkpoints can be generated once and served to everyone forever; a remark tying
*this* screen to *this* reader's own wrong turns cannot be, so it is paid for per
screen.

Everything here fails soft. A companion is an extra, not the lesson: if the
model is slow, unreachable, unconfigured, or returns something unusable, the
endpoint returns `{"message": null}` and the reader simply doesn't get a remark
on that screen. Nothing about the lesson depends on it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import coach
from app.auth import get_current_user
from app.db import get_db
from app.llm import LLMError, is_configured
from app.models import Lesson, LessonQuestion, Profile, QuestionResponse
from app.schemas import CoachIn, CoachOut
from app.sections import split_sections

router = APIRouter()
log = logging.getLogger(__name__)

# Enough for the companion to spot a pattern, few enough that the prompt stays
# small and the reader's distant past doesn't crowd out what they did just now.
HISTORY_LIMIT = 20


def recorded_history(
    db: Session, user_id: str, limit: int = HISTORY_LIMIT
) -> list[coach.PastAnswer]:
    """What this reader has on record, most recent first.

    Answers from *earlier* sessions only, which is all there ever is: an
    attempt doesn't become a `QuestionResponse` until the lesson it belongs to
    is completed. The session in progress is deliberately invisible here.

    Shared with the chat router, which needs exactly the same view.
    """
    rows = db.execute(
        select(QuestionResponse, LessonQuestion)
        .join(LessonQuestion, LessonQuestion.id == QuestionResponse.question_id)
        .where(QuestionResponse.user_id == user_id)
        .order_by(QuestionResponse.answered_at.desc())
        .limit(limit)
    ).all()

    return [
        coach.PastAnswer(
            concept=question.concept or "general",
            question=question.prompt,
            correct=response.correct,
            misconception=response.misconception,
        )
        for response, question in rows
    ]


@router.post("/coach", response_model=CoachOut)
def coach_message(
    payload: CoachIn,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoachOut:
    if not is_configured():
        return CoachOut(message=None)

    lesson = db.scalar(
        select(Lesson).where(Lesson.slug == payload.slug, Lesson.published.is_(True))
    )
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")

    sections = split_sections(lesson.body)
    section = next((s for s in sections if s.index == payload.section_index), None)
    if section is None:
        return CoachOut(message=None)

    # Earlier sessions only. What the reader has answered *during* this lesson
    # is deliberately not here: those attempts aren't on record yet, and a tip
    # shaped by the checkpoint two screens back is a step away from being about
    # their performance rather than about the material.
    history = recorded_history(db, profile.id)

    try:
        message = coach.generate(
            lesson.title,
            section.heading,
            section.body,
            history,
            # Stable per reader and section, so a re-read shows what it showed
            # before rather than flickering.
            seed=f"{profile.id}:{lesson.slug}:{payload.section_index}",
            already_shown=payload.shown_tips,
            area_ratings=profile.area_ratings,
        )
    except LLMError as exc:
        # A missing remark is a non-event. Never surface it to the reader.
        log.info("no coach message for %s/%s: %s", lesson.slug, payload.section_index, exc)
        return CoachOut(message=None)

    return CoachOut(message=message)
