import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import SessionLocal
from app.llm import is_configured
from app.models import Lesson, LessonQuestion
from app.routers import lessons, profile, questions, stats

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(title="Learn Diabetes", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile.router, prefix="/api", tags=["profile"])
app.include_router(lessons.router, prefix="/api", tags=["lessons"])
app.include_router(questions.router, prefix="/api", tags=["questions"])
app.include_router(stats.router, prefix="/api", tags=["stats"])


@app.get("/api/health")
def health() -> dict:
    """Liveness plus the things that most often aren't set up yet."""
    log = logging.getLogger(__name__)
    db_ok, schema_ok, lesson_count, question_count = False, False, 0, 0

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_ok = True
            # A missing table is a setup step, not an unreachable database.
            try:
                lesson_count = db.scalar(select(func.count()).select_from(Lesson)) or 0
                schema_ok = True
            except SQLAlchemyError:
                db.rollback()
                log.warning("lessons table is missing — run backend/sql/schema.sql")

            if schema_ok:
                try:
                    question_count = (
                        db.scalar(select(func.count()).select_from(LessonQuestion)) or 0
                    )
                except SQLAlchemyError:
                    db.rollback()
                    log.warning(
                        "lesson_questions table is missing — run backend/sql/002_questions.sql"
                    )
    except SQLAlchemyError as exc:
        log.warning("health check could not reach the database: %s", exc)

    return {
        "ok": db_ok and schema_ok and lesson_count > 0,
        "database_reachable": db_ok,
        "schema_applied": schema_ok,
        "lessons_published": lesson_count,
        "checkpoint_questions": question_count,
        "auth_configured": bool(settings.supabase_url or settings.supabase_jwt_secret),
        # Checkpoints are optional: without a key the lessons still read, they
        # just have no questions in them.
        "question_generation_configured": is_configured(),
    }
