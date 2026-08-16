"""Derived learner statistics. Pure functions here, queries in the router."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
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


@dataclass(frozen=True)
class FirstTry:
    """One checkpoint, the first time this reader answered it.

    Only first attempts, because the retry is coaching rather than assessment:
    a reader who is shown what they got wrong and then picks the right option
    has not demonstrated anything the first attempt didn't already say.
    """

    lesson_slug: str
    lesson_title: str
    category: str
    correct: bool


@dataclass(frozen=True)
class TroubleSpot:
    """A lesson whose checkpoints this reader has actually missed."""

    lesson_slug: str
    lesson_title: str
    category: str
    asked: int
    missed: int


def rank_trouble_spots(tries: list[FirstTry], limit: int = 3) -> list[TroubleSpot]:
    """The lessons worth going back to, hardest first.

    Ranked by how many checkpoints were missed rather than by hit rate, so a
    lesson missed four times out of eight outranks one missed once out of one —
    a single wrong answer is noise, and the dashboard has room for three rows.
    Ties break on the proportion missed, then on title so the order is stable
    between requests.
    """
    grouped: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for t in tries:
        counts = grouped[(t.lesson_slug, t.lesson_title, t.category)]
        counts[0] += 1
        if not t.correct:
            counts[1] += 1

    spots = [
        TroubleSpot(slug, title, category, asked, missed)
        for (slug, title, category), (asked, missed) in grouped.items()
        if missed
    ]
    spots.sort(key=lambda s: (-s.missed, -s.missed / s.asked, s.lesson_title))
    return spots[:limit]
