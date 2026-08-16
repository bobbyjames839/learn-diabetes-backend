from datetime import date

from app.stats import FirstTry, compute_streak, percent, rank_trouble_spots


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


def tries(slug: str, right: int, wrong: int) -> list[FirstTry]:
    return [FirstTry(slug, slug.title(), "basics", True) for _ in range(right)] + [
        FirstTry(slug, slug.title(), "basics", False) for _ in range(wrong)
    ]


def test_no_answers_means_no_trouble_spots():
    assert rank_trouble_spots([]) == []


def test_a_lesson_answered_perfectly_is_not_a_trouble_spot():
    assert rank_trouble_spots(tries("carbs", right=4, wrong=0)) == []


def test_counts_are_first_tries_asked_and_missed():
    [spot] = rank_trouble_spots(tries("carbs", right=3, wrong=2))
    assert (spot.lesson_slug, spot.asked, spot.missed) == ("carbs", 5, 2)


def test_ranked_by_misses_not_by_hit_rate():
    """One wrong answer out of one is noise; four out of eight is a weak area."""
    spots = rank_trouble_spots(tries("insulin", 4, 4) + tries("ketones", 0, 1))
    assert [s.lesson_slug for s in spots] == ["insulin", "ketones"]


def test_equal_misses_break_on_proportion_then_title():
    spots = rank_trouble_spots(tries("aaa", 8, 2) + tries("zzz", 0, 2) + tries("mmm", 0, 2))
    assert [s.lesson_slug for s in spots] == ["mmm", "zzz", "aaa"]


def test_only_the_worst_few_are_returned():
    answers = [t for i in range(5) for t in tries(f"lesson{i}", 0, 5 - i)]
    assert [s.lesson_slug for s in rank_trouble_spots(answers)] == [
        "lesson0",
        "lesson1",
        "lesson2",
    ]
