"""Where a reader stands, area by area, on a scale of 1 to 100.

The profile says what the reader *told us* about themselves — why they're here,
how they like to be taught, what they want to focus on. This says what their
answers have actually shown, and it is the part that moves. One number per area
of diabetes management, nudged every time a lesson session or a chat session
produces evidence, and read back into every prompt that is handed the profile.

Why a dict of numbers rather than a list of weak spots:

- It is the same shape for a reader with no history as for one with a year of
  it. Everything starts at `DEFAULT_RATING` — the honest "we don't know yet"
  answer, in the middle — so a prompt never has to special-case the newcomer.
- It survives the evidence it came from. `question_responses` keeps every
  attempt, but a rolling window of twenty answers is all any prompt can read;
  the rating is what the ones before that leave behind.
- It moves both ways. A weak spot list only ever grows, so it silently becomes
  a list of things the reader has since learned. A number that rises when they
  get things right retires itself.

The areas are a closed, hand-defined set, kept deliberately small: every one of
them has to be reachable by evidence, or it is a row in a table that never
changes. Each maps from a lesson category, a chat topic, or both. This is the
coarse-grained cousin of the concept taxonomy `lesson_questions.concept` still
needs — an area is "insulin action", a concept would be "the tail of a bolus".

Pure functions, no database import, tested the way `app/learner.py` and
`app/stats.py` are. The routers do the counting and the writing.
"""

from __future__ import annotations

# Every area a reader can be rated on, with the words a prompt should use for
# it. Small on purpose: an area nothing can produce evidence for would sit at
# the default forever and teach the model nothing.
AREAS: dict[str, str] = {
    "glucose_basics": "what makes blood glucose rise and fall",
    "carb_counting": "counting carbohydrate",
    "insulin_action": "how insulin acts over time",
    "exercise": "exercise and activity",
    "highs_lows": "highs, lows and how they resolve",
    "ketones_sick_days": "ketones and illness",
}

# The middle of the scale: what we assume before anyone has answered anything.
DEFAULT_RATING = 50
FLOOR = 1
CEILING = 100

# How far one piece of evidence can move a rating, and how fast it gets there.
# A single answer is worth a nudge; a whole lesson's checkpoints are worth a
# real move; nothing is ever worth more than half the distance, because one
# good session does not make someone an expert and one bad one does not undo a
# month.
_WEIGHT_PER_ANSWER = 0.12
_MAX_WEIGHT = 0.5

# Where evidence comes from. Lesson categories are the app's own vocabulary and
# chat topics are the reader's; both are closed sets, so a mapping that stops
# matching is a test failure rather than a silent no-op (see tests).
CATEGORY_AREAS: dict[str, str] = {
    "basics": "glucose_basics",
    "food": "carb_counting",
    "insulin": "insulin_action",
    "exercise": "exercise",
    "troubleshooting": "highs_lows",
    # No `daily-life` entry: the frontend has the category but no lesson is
    # authored in it, so an area for it would sit at the default forever.
    # Author one and this is the line to add back, alongside its area.
}

# `tutor_picks` is deliberately absent: it means the reader didn't name a
# subject, so there is nothing to attribute the session to up front. The recap
# generator names the area it actually turned out to be about instead.
TOPIC_AREAS: dict[str, str] = {
    "carb_counting": "carb_counting",
    "insulin_action": "insulin_action",
    "exercise": "exercise",
    "highs_lows": "highs_lows",
    "ketones_sick_days": "ketones_sick_days",
}


def with_defaults(stored: dict | None) -> dict[str, int]:
    """The full rating map, whatever is actually in the column.

    Stored ratings are merged over a full set of defaults, and anything that
    isn't a known area is dropped. So adding an area to `AREAS` gives every
    existing reader a sensible starting value with no migration, and removing
    one takes it out of every prompt immediately.
    """
    ratings = {area: DEFAULT_RATING for area in AREAS}
    for area, value in (stored or {}).items():
        if area in ratings and isinstance(value, (int, float)) and not isinstance(value, bool):
            ratings[area] = _clamp(round(value))
    return ratings


def blend(current: int, correct: int, total: int) -> int:
    """One area's rating, moved towards what this session's answers showed.

    A weighted step rather than a replacement: the rating is a running view of
    someone's understanding, and an afternoon of tired guessing is evidence,
    not a verdict. The weight grows with the number of answers behind it, so
    one lucky checkpoint barely registers and a whole lesson counts properly.
    """
    if total <= 0:
        return _clamp(current)
    observed = 100 * correct / total
    weight = min(_MAX_WEIGHT, _WEIGHT_PER_ANSWER * total)
    return _clamp(round(current + weight * (observed - current)))


def apply_evidence(stored: dict | None, evidence: dict[str, tuple[int, int]]) -> dict[str, int]:
    """The new rating map after a session, as `{area: (correct, total)}`.

    Returns the whole map, defaults included, because that is what gets stored:
    a partial column would mean every reader's ratings depended on when they
    signed up relative to the last time `AREAS` changed.
    """
    ratings = with_defaults(stored)
    for area, (correct, total) in evidence.items():
        if area in ratings:
            ratings[area] = blend(ratings[area], correct, total)
    return ratings


def band(rating: int) -> str:
    """The rating in words, which is what a prompt should actually act on."""
    if rating < 35:
        return "shaky"
    if rating < 55:
        return "finding their feet"
    if rating < 75:
        return "solid"
    return "strong"


def describe(stored: dict | None) -> str:
    """The rating map as prompt text, weakest first.

    Weakest first because that is the order the model should be considering
    them in: the top of this list is the reason the session exists. The
    instruction against reading the numbers out loud is not decoration — a
    model handed a table of scores about the person it is talking to will
    otherwise open with it, and "you're at 38 on carb counting" is a report
    card, not teaching.
    """
    ratings = with_defaults(stored)
    lines = [
        "--- WHERE THEY STAND (1-100, built from what they have answered here,",
        "--- 50 means we have nothing to go on yet) ---",
    ]
    for area, rating in sorted(ratings.items(), key=lambda kv: (kv[1], kv[0])):
        lines.append(f"- {AREAS[area]}: {rating} ({band(rating)})")
    lines.append(
        "Use this to choose what to work on and how hard to pitch it — start "
        "where they are weakest unless they have asked for something else. "
        "Never say the numbers, the bands, or that any such rating exists."
    )
    return "\n".join(lines)


def _clamp(value: int) -> int:
    return max(FLOOR, min(CEILING, value))
