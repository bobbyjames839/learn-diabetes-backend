from collections import defaultdict
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Lesson, LessonProgress, Profile, utcnow
from app.routers.lessons import _summary
from app.schemas import CategoryStat, StatsOut
from app.stats import compute_streak, percent

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
    )
