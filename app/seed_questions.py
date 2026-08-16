"""Generate checkpoint questions for lessons, ahead of any reader.

The API will generate a lesson's questions on first view, but that makes one
unlucky reader wait. Run this after seeding new lessons and nobody does.

    uv run python -m app.seed_questions              # lessons with none yet
    uv run python -m app.seed_questions --slug what-is-glucose
    uv run python -m app.seed_questions --force      # regenerate everything
    uv run python -m app.seed_questions --dry-run    # print, write nothing
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import delete, select

from app import question_gen
from app.config import get_settings
from app.db import SessionLocal
from app.llm import LLMError, is_configured
from app.models import Lesson, LessonQuestion
from app.sections import split_sections

log = logging.getLogger("seed_questions")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", action="append", help="only this lesson (repeatable)")
    parser.add_argument(
        "--force", action="store_true", help="replace questions that already exist"
    )
    parser.add_argument("--dry-run", action="store_true", help="print instead of writing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not is_configured():
        log.error("OPENROUTER_API_KEY is not set — nothing to generate with.")
        return 1

    model = get_settings().question_model
    written = skipped = failed = 0

    with SessionLocal() as db:
        query = select(Lesson).order_by(Lesson.order_index)
        if args.slug:
            query = query.where(Lesson.slug.in_(args.slug))
        lessons = list(db.scalars(query).all())

        if not lessons:
            log.error("No matching lessons. Has the lesson seed SQL been applied?")
            return 1

        for lesson in lessons:
            existing = list(
                db.scalars(
                    select(LessonQuestion).where(LessonQuestion.lesson_id == lesson.id)
                ).all()
            )
            if existing and not args.force:
                log.info("· %-34s %d question(s) already — skipping", lesson.slug, len(existing))
                skipped += 1
                continue

            sections = split_sections(lesson.body)
            if not sections:
                log.warning("· %-34s no sections to question — skipping", lesson.slug)
                skipped += 1
                continue

            try:
                generated = question_gen.generate(lesson.title, lesson.summary, sections)
            except (LLMError, ValueError) as exc:
                log.error("✗ %-34s %s", lesson.slug, exc)
                failed += 1
                continue

            if args.dry_run:
                log.info("\n%s — %d question(s)", lesson.slug, len(generated))
                for question in generated:
                    log.info("  [%d] %s", question.section_index, question.prompt)
                    for option in question.options:
                        mark = "✓" if option.correct else " "
                        note = "" if option.correct else f"   ← {option.misconception}"
                        log.info("      %s %s%s", mark, option.text, note)
                continue

            if existing:
                db.execute(
                    delete(LessonQuestion).where(LessonQuestion.lesson_id == lesson.id)
                )
                db.flush()

            db.add_all(
                LessonQuestion(
                    lesson_id=lesson.id,
                    section_index=q.section_index,
                    concept=q.concept,
                    prompt=q.prompt,
                    options=[o.model_dump() for o in q.options],
                    explanation=q.explanation,
                    model=model,
                )
                for q in generated
            )
            db.commit()
            log.info("✓ %-34s %d question(s)", lesson.slug, len(generated))
            written += 1

    log.info("\n%d written, %d skipped, %d failed", written, skipped, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
