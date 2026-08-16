"""Checkpoint questions inside a lesson, and the answers readers give.

A checkpoint is the teacher stopping to ask. Getting it wrong is not the end of
the exchange: the first wrong answer is met with coaching aimed at the exact
misconception it revealed, and the reader tries again. Only when the checkpoint
is *settled* — answered right, or attempts spent — does the answer key cross the
wire, because a retry offered alongside the answer is not a retry.

This router still never blocks a lesson: if questions can't be generated the
endpoint returns an empty list and the lesson reads exactly as it did before.

Answering a checkpoint does not touch the database. A reader who abandons a
lesson mid-way should leave nothing behind, so an attempt is only a record
once the reader marks the lesson complete — see `set_progress` in
`app.routers.lessons`, which persists the whole session's attempts in one
batch. This endpoint just judges an attempt against the answer key and
returns the verdict; the client carries the session's state (which options
have been tried) because the server has nowhere of its own to keep it yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import question_gen
from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.llm import LLMError, is_configured
from app.models import Lesson, LessonQuestion, Profile, QuestionResponse
from app.schemas import (
    AnswerIn,
    AnswerResult,
    ConceptInsight,
    InsightsOut,
    QuestionOption,
    QuestionOut,
)
from app.sections import split_sections

router = APIRouter()
log = logging.getLogger(__name__)


# One wrong answer earns coaching and another go. A third would stop being
# teaching and start being a process of elimination.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class Attempt:
    """An in-session attempt that hasn't (and may never) become a `QuestionResponse`."""

    chosen_index: int
    correct: bool


def _correct_index(question: LessonQuestion) -> int:
    for i, option in enumerate(question.options or []):
        if option.get("correct"):
            return i
    return -1


def _result(
    question: LessonQuestion, attempts: list[QuestionResponse | Attempt]
) -> AnswerResult | None:
    """How the checkpoint stands for one reader, after the attempts they've made.

    Settled means there is nothing left to try: they got it right, they spent
    their attempts, or the option they picked carries no coaching to retry on —
    which is how questions generated before coaching existed still behave
    sensibly, as one-shot questions.
    """
    if not attempts:
        return None

    latest = attempts[-1]
    coaching = None
    if not latest.correct:
        options = question.options or []
        if latest.chosen_index < len(options):
            coaching = options[latest.chosen_index].get("coaching")

    settled = latest.correct or len(attempts) >= MAX_ATTEMPTS or not coaching

    return AnswerResult(
        question_id=question.id,
        chosen_index=latest.chosen_index,
        correct=latest.correct,
        attempt=len(attempts),
        settled=settled,
        correct_index=_correct_index(question) if settled else None,
        explanation=question.explanation if settled else None,
        coaching=coaching,
    )


def _to_out(question: LessonQuestion, attempts: list[QuestionResponse]) -> QuestionOut:
    """Strip the answer key. Only `text` crosses the wire before an answer."""
    return QuestionOut(
        id=question.id,
        section_index=question.section_index,
        prompt=question.prompt,
        options=[
            QuestionOption(index=i, text=option.get("text", ""))
            for i, option in enumerate(question.options or [])
        ],
        answered=_result(question, attempts),
        tried_indices=[a.chosen_index for a in attempts if not a.correct],
    )


def _existing(db: Session, lesson_id: str) -> list[LessonQuestion]:
    return list(
        db.scalars(
            select(LessonQuestion)
            .where(LessonQuestion.lesson_id == lesson_id)
            .order_by(LessonQuestion.section_index)
        ).all()
    )


def _generate_and_store(db: Session, lesson: Lesson) -> list[LessonQuestion]:
    """Generate this lesson's checkpoints once and keep them."""
    sections = split_sections(lesson.body)
    if not sections:
        return []

    generated = question_gen.generate(lesson.title, lesson.summary, sections)

    db.add_all(
        LessonQuestion(
            lesson_id=lesson.id,
            section_index=q.section_index,
            concept=q.concept,
            prompt=q.prompt,
            options=[o.model_dump() for o in q.options],
            explanation=q.explanation,
            model=get_settings().question_model,
        )
        for q in generated
    )

    try:
        db.commit()
    except IntegrityError:
        # Another request generated this lesson's set first. Theirs is as good
        # as ours — drop ours and read back what landed.
        db.rollback()
        log.info("questions for %s were generated concurrently", lesson.slug)

    return _existing(db, lesson.id)


@router.get("/lessons/{slug}/questions", response_model=list[QuestionOut])
def lesson_questions(
    slug: str,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[QuestionOut]:
    lesson = db.scalar(select(Lesson).where(Lesson.slug == slug, Lesson.published.is_(True)))
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")

    questions = _existing(db, lesson.id)

    # First reader of a lesson generates the set. Run `python -m app.seed_questions`
    # ahead of time and nobody ever waits.
    if not questions and is_configured():
        try:
            questions = _generate_and_store(db, lesson)
        except (LLMError, ValueError) as exc:
            # A lesson that reads fine without checkpoints beats an error page.
            db.rollback()
            log.warning("could not generate questions for %s: %s", lesson.slug, exc)
            return []

    if not questions:
        return []

    # Only lessons finished at least once have any responses at all now — see
    # `set_progress`. A lesson still in progress reads back with a clean slate.
    attempts: dict[str, list[QuestionResponse]] = {}
    for response in db.scalars(
        select(QuestionResponse)
        .where(
            QuestionResponse.user_id == profile.id,
            QuestionResponse.question_id.in_([q.id for q in questions]),
        )
        .order_by(QuestionResponse.attempt)
    ).all():
        attempts.setdefault(response.question_id, []).append(response)

    return [_to_out(q, attempts.get(q.id, [])) for q in questions]


@router.post("/questions/{question_id}/answer", response_model=AnswerResult)
def answer_question(
    question_id: str,
    payload: AnswerIn,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnswerResult:
    """Judge one attempt. Nothing here is written to the database.

    A checkpoint only becomes a permanent `QuestionResponse` once the lesson is
    marked complete, so this just scores the attempt against the answer key —
    the client's `tried_indices` stands in for the record the server doesn't
    keep yet.
    """
    question = db.get(LessonQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found.")

    options = question.options or []
    if payload.chosen_index >= len(options):
        raise HTTPException(status_code=400, detail="That option does not exist.")

    # A lesson finished earlier already has its answers on record — replay that
    # verdict rather than letting a revisit relitigate a settled checkpoint.
    saved = list(
        db.scalars(
            select(QuestionResponse)
            .where(
                QuestionResponse.user_id == profile.id,
                QuestionResponse.question_id == question.id,
            )
            .order_by(QuestionResponse.attempt)
        ).all()
    )
    settled_already = _result(question, saved)
    if settled_already and settled_already.settled:
        return settled_already

    # The retry is a second thought, not a second guess at the same door.
    if payload.chosen_index in payload.tried_indices:
        raise HTTPException(status_code=400, detail="You have already tried that one.")

    chosen = options[payload.chosen_index]
    attempt = Attempt(chosen_index=payload.chosen_index, correct=bool(chosen.get("correct")))

    result = _result(question, [*saved, attempt])
    assert result is not None  # an attempt was just made
    return result


@router.get("/insights", response_model=InsightsOut)
def insights(
    profile: Profile = Depends(get_current_user), db: Session = Depends(get_db)
) -> InsightsOut:
    """What this reader's answers say about them, rolled up per concept.

    Nothing renders this yet — it is the input the interactive practice will
    build on, exposed now so the data can be checked as it accumulates.
    """
    rows = db.execute(
        select(QuestionResponse, LessonQuestion.concept)
        .join(LessonQuestion, LessonQuestion.id == QuestionResponse.question_id)
        .where(QuestionResponse.user_id == profile.id)
        .order_by(QuestionResponse.answered_at.desc())
    ).all()

    by_concept: dict[str, ConceptInsight] = {}
    for response, concept in rows:
        insight = by_concept.setdefault(
            concept or "general", ConceptInsight(concept=concept or "general", asked=0, correct=0, misconceptions=[])
        )
        insight.asked += 1
        if response.correct:
            insight.correct += 1
        elif response.misconception and response.misconception not in insight.misconceptions:
            insight.misconceptions.append(response.misconception)

    return InsightsOut(
        questions_answered=len(rows),
        questions_correct=sum(1 for r, _ in rows if r.correct),
        # Weakest first — that is the order the next phase will want to work in.
        by_concept=sorted(by_concept.values(), key=lambda c: (c.correct / c.asked, -c.asked)),
    )
