"""What finishing a lesson tells us about the learner.

The onboarding quiz asks five questions once, on day one, and then never again.
Two of those answers go stale in opposite ways: someone who called themselves
"new" is not new after a dozen lessons, and someone who answered "not sure" to
what they most want to understand has since spent three sessions getting
exercise questions wrong. Finishing a lesson is the moment there is new evidence
about both, so it is the moment the profile is revised.

The revision is deterministic — counting, not a model call. The chatbot may also
revise the profile (see `app/chat.py`), and it is the one that gets to interpret;
this half only promotes what the numbers plainly show.

Two rules about what is NOT touched, both of which matter more than what is:

- Experience only ever goes up. It is their claim about what they know, and
  knowledge does not go backwards because of one bad afternoon.
- Focus is only ever *filled in*, never overwritten. It asks what they most want
  to understand — a want, not a weakness. Reading "you keep missing exercise
  questions" as "exercise is what you want to learn" gets the question wrong,
  so evidence is only allowed to answer it when they said "not sure".

Pure logic, no database import, tested the way `app/stats.py` is. The router
does the counting and hands the result in.
"""

from __future__ import annotations

from typing import NamedTuple

# Lesson categories are the app's own vocabulary; onboarding focus values are
# the reader's. Only the categories that map cleanly onto something a reader
# said they might want appear here — `basics` is deliberately absent, because
# missing introductory questions says nothing about what to focus on.
CATEGORY_TO_FOCUS = {
    "food": "carb_counting",
    "insulin": "insulin_action",
    "troubleshooting": "highs_lows",
}

# Enough misses in one area to call it an area rather than a bad question.
FOCUS_EVIDENCE = 3

# Lessons finished, and the accuracy to go with them, before we stop calling
# someone new. Accuracy matters as much as volume: clicking through twelve
# lessons getting half of them wrong is not experience.
BASICS_AT = (3, 0.6)
EXPERIENCED_AT = (10, 0.75)

_LADDER = ["new", "basics", "experienced"]


class LearnerEvidence(NamedTuple):
    """Everything the revision looks at, counted by the caller."""

    lessons_completed: int
    questions_asked: int
    questions_correct: int
    # Wrong attempts per lesson category, across every lesson they have done.
    wrong_by_category: dict[str, int]


def _accuracy(evidence: LearnerEvidence) -> float:
    """Share of checkpoint attempts answered correctly, 0.0 with nothing asked.

    Zero rather than one for a reader with no answers: an unproven reader is
    not an experienced one, and this only ever gates promotion.
    """
    if evidence.questions_asked <= 0:
        return 0.0
    return evidence.questions_correct / evidence.questions_asked


def next_experience(current: str | None, evidence: LearnerEvidence) -> str | None:
    """The experience level they have earned, or None to leave it alone.

    Never returns a level below the current one — see the module docstring.
    """
    accuracy = _accuracy(evidence)

    earned = "new"
    if evidence.lessons_completed >= BASICS_AT[0] and accuracy >= BASICS_AT[1]:
        earned = "basics"
    if evidence.lessons_completed >= EXPERIENCED_AT[0] and accuracy >= EXPERIENCED_AT[1]:
        earned = "experienced"

    # An unrecognised current value is someone else's data, not a rung on this
    # ladder. Leave it be rather than guessing where it sits.
    if current not in _LADDER:
        return None
    if _LADDER.index(earned) <= _LADDER.index(current):
        return None
    return earned


def inferred_focus(current: str | None, evidence: LearnerEvidence) -> str | None:
    """A focus to fill in for a reader who said "not sure", or None.

    Only ever fills the gap they left. A reader who named a focus keeps it, and
    a reader with no clear weak area keeps "not sure" — which is an honest
    answer, and better than a made-up one.
    """
    if current != "not_sure":
        return None

    ranked = sorted(
        (
            (count, CATEGORY_TO_FOCUS[category])
            for category, count in evidence.wrong_by_category.items()
            if category in CATEGORY_TO_FOCUS
        ),
        # Most-missed first; category name as a stable tie-break, so two areas
        # level on misses don't flip between sessions.
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not ranked or ranked[0][0] < FOCUS_EVIDENCE:
        return None
    return ranked[0][1]


def profile_revisions(
    experience: str | None, focus: str | None, evidence: LearnerEvidence
) -> dict[str, str]:
    """The profile changes this session has earned — usually none.

    Returns only fields that actually change, so the caller can skip the write
    entirely on the common path where finishing one more lesson has told us
    nothing new.
    """
    revisions: dict[str, str] = {}

    earned = next_experience(experience, evidence)
    if earned:
        revisions["onboarding_experience"] = earned

    inferred = inferred_focus(focus, evidence)
    if inferred:
        revisions["onboarding_focus"] = inferred

    return revisions
