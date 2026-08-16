from datetime import date

from app.stats import compute_streak, percent


def d(day: int) -> date:
    return date(2026, 8, day)


def test_no_completions_is_no_streak():
    assert compute_streak(set(), d(16)) == 0


def test_counts_consecutive_days_ending_today():
    assert compute_streak({d(14), d(15), d(16)}, d(16)) == 3


def test_gap_breaks_the_streak():
    assert compute_streak({d(11), d(12), d(15), d(16)}, d(16)) == 2


def test_yesterday_still_counts_so_it_survives_the_morning():
    """A streak shouldn't disappear just because today's lesson isn't done yet."""
    assert compute_streak({d(14), d(15)}, d(16)) == 2


def test_two_days_stale_is_broken():
    assert compute_streak({d(13), d(14)}, d(16)) == 0


def test_duplicate_and_future_days_do_not_inflate():
    assert compute_streak({d(16), d(17)}, d(16)) == 1


def test_percent_rounds_and_handles_empty_curriculum():
    assert percent(0, 0) == 0
    assert percent(1, 3) == 33
    assert percent(2, 3) == 67
    assert percent(5, 5) == 100
