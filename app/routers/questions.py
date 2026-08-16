"""Checkpoint questions inside a lesson, and the answers readers give.

Reading is the primary experience here — a checkpoint is a beat in it, not a
gate. So this router never blocks a lesson: if questions can't be generated the
endpoint returns an empty list and the lesson reads exactly as it did before.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
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


def _correct_index(question: LessonQuestion) -> int:
    for i, option in enumerate(question.options or []):
        if option.get("correct"):
            return i
    return -1


def _to_out(question: LessonQuestion, answer: QuestionResponse | None) -> QuestionOut:
    """Strip the answer key. Only `text` crosses the wire before an answer."""
    return QuestionOut(
        id=question.id,
        section_index=question.section_index,
        prompt=question.prompt,
        options=[
            QuestionOption(index=i, text=option.get("text", ""))
            for i, option in enumerate(question.options or [])
        ],
        answered=(
            AnswerResult(
                question_id=question.id,
                chosen_index=answer.chosen_index,
                correct=answer.correct,
                correct_index=_correct_index(question),
                explanation=question.explanation,
            )
            if answer
            else None
        ),
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

    answers = {
        r.question_id: r
        for r in db.scalars(
            select(QuestionResponse)
            .where(
                QuestionResponse.user_id == profile.id,
                QuestionResponse.question_id.in_([q.id for q in questions]),
            )
            .order_by(QuestionResponse.answered_at)
        ).all()
    }
    return [_to_out(q, answers.get(q.id)) for q in questions]


@router.post("/questions/{question_id}/answer", response_model=AnswerResult)
def answer_question(
    question_id: str,
    payload: AnswerIn,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnswerResult:
    question = db.get(LessonQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found.")

    options = question.options or []
    if payload.chosen_index >= len(options):
        raise HTTPException(status_code=400, detail="That option does not exist.")

    chosen = options[payload.chosen_index]
    correct = bool(chosen.get("correct"))

    prior = (
        db.scalar(
            select(func.count())
            .select_from(QuestionResponse)
            .where(
                QuestionResponse.user_id == profile.id,
                QuestionResponse.question_id == question.id,
            )
        )
        or 0
    )

    db.add(
        QuestionResponse(
            user_id=profile.id,
            question_id=question.id,
            chosen_index=payload.chosen_index,
            correct=correct,
            # The observation worth keeping: not that they were wrong, but which
            # wrong model of glucose they were holding.
            misconception=None if correct else chosen.get("misconception"),
            attempt=prior + 1,
        )
    )
    db.commit()

    return AnswerResult(
        question_id=question.id,
        chosen_index=payload.chosen_index,
        correct=correct,
        correct_index=_correct_index(question),
        explanation=question.explanation,
    )


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
