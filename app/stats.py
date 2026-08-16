"""Derived learner statistics. Pure functions here, queries in the router."""

from __future__ import annotations

from datetime import date, timedelta


def compute_streak(completion_days: set[date], today: date) -> int:
    """Consecutive days ending today (or yesterday) on which a lesson was completed.

    Yesterday still counts so the streak doesn't vanish before the user has had a
    chance to study today.
    """
    if not completion_days:
        return 0

    if today in completion_days:
        cursor = today
    elif (today - timedelta(days=1)) in completion_days:
        cursor = today - timedelta(days=1)
    else:
        return 0

    streak = 0
    while cursor in completion_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def percent(completed: int, total: int) -> int:
    return round(completed / total * 100) if total else 0
