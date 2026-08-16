from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app import mastery

# Fixed option sets for the onboarding quiz — multiple choice, not free text,
# so responses stay comparable across readers and there's nothing to sanitise.
OnboardingGoal = Literal["newly_diagnosed", "managing_long_term", "caregiver", "curious"]
OnboardingExperience = Literal["new", "basics", "experienced"]
OnboardingLearningStyle = Literal["quick_bites", "deep_dives", "mixed"]
OnboardingContentPreference = Literal["why_first", "examples_first"]
OnboardingFocus = Literal["carb_counting", "insulin_action", "exercise", "highs_lows", "not_sure"]


class ProfileOut(BaseModel):
    id: str
    email: str | None
    display_name: str | None
    diagnosed_year: int | None
    created_at: datetime
    onboarding_goal: str | None
    onboarding_experience: str | None
    onboarding_learning_style: str | None
    onboarding_content_preference: str | None
    onboarding_focus: str | None
    # NULL means the quiz hasn't run yet — the frontend's cue to show it.
    onboarding_completed_at: datetime | None
    # Where they stand per area, 1-100 — the part of the profile the reader
    # doesn't write. Always the full map: see `app/mastery.py`.
    area_ratings: dict[str, int] = Field(default_factory=dict)

    @field_validator("area_ratings", mode="before")
    @classmethod
    def _full_map(cls, value: object) -> dict[str, int]:
        # The column holds whatever the last session wrote; the client always
        # gets every area, defaults filled in, so it never has to know which
        # ones happen to have evidence behind them.
        return mastery.with_defaults(value if isinstance(value, dict) else None)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    diagnosed_year: int | None = Field(default=None, ge=1900, le=2100)


class OnboardingIn(BaseModel):
    """The quiz is answered in one go — there's no partial state to resume."""

    goal: OnboardingGoal
    experience: OnboardingExperience
    learning_style: OnboardingLearningStyle
    content_preference: OnboardingContentPreference
    focus: OnboardingFocus


class LessonSummary(BaseModel):
    """List view — no body, so the lessons index stays small."""

    slug: str
    title: str
    summary: str
    category: str
    difficulty: int
    estimated_minutes: int
    order_index: int
    completed: bool
    last_viewed_at: datetime | None


class LessonSection(BaseModel):
    """One readable chunk of a lesson, split server-side.

    The reader and the checkpoints have to agree on where sections begin, so the
    split happens once here rather than being reimplemented in the frontend.
    """

    index: int
    heading: str
    markdown: str


class LessonDetail(LessonSummary):
    body: str
    key_takeaways: list[str]
    sections: list[LessonSection]


class QuestionOption(BaseModel):
    """What the client is allowed to see: the text, and nothing else.

    `correct` and `misconception` stay server-side — sending them would put the
    answer key in the page source.
    """

    index: int
    text: str


class AnswerResult(BaseModel):
    """What comes back from an attempt.

    A wrong first attempt is answered with coaching and another go, so the
    answer key stays server-side until the checkpoint is settled — sending
    `correct_index` alongside a retry would make the retry meaningless.
    """

    question_id: str
    chosen_index: int
    correct: bool
    attempt: int
    settled: bool
    # Only once settled: answered right, or attempts spent.
    correct_index: int | None = None
    explanation: str | None = None
    # The teacher's reply to this particular wrong turn. Present on any wrong
    # answer, whether or not a retry remains.
    coaching: str | None = None


class QuestionOut(BaseModel):
    id: str
    section_index: int
    prompt: str
    options: list[QuestionOption]
    # Present only once this reader has attempted it, so a revisited lesson
    # shows where they got to rather than a blank slate.
    answered: AnswerResult | None = None
    # Wrong options this reader has already spent, so a resumed retry offers
    # what is left rather than the full set again.
    tried_indices: list[int] = []


class AnswerIn(BaseModel):
    chosen_index: int = Field(ge=0)
    # Wrong options already spent this session. Nothing is persisted until the
    # lesson is completed, so the server has no record of its own to check attempt
    # count or settledness against — the client carries it instead.
    tried_indices: list[int] = Field(default_factory=list)


class ConceptInsight(BaseModel):
    concept: str
    asked: int
    correct: int
    # The wrong turns this reader took, most recent first — the raw material for
    # the interactive practice that comes next.
    misconceptions: list[str]


class InsightsOut(BaseModel):
    questions_answered: int
    questions_correct: int
    by_concept: list[ConceptInsight]


class AnswerAttemptIn(BaseModel):
    """One checkpoint attempt made during the session.

    Submitted as a batch alongside completion — a checkpoint isn't a permanent
    record of what a reader believed until the lesson it belongs to is.
    """

    question_id: str
    chosen_index: int = Field(ge=0)


class CoachIn(BaseModel):
    """The screen the reader is on.

    Notably absent: what they've answered so far in this session. Tips are
    shaped by answers from *earlier* sessions — what's on record — and nothing
    else. The current session's attempts stay client-side until it ends, and a
    tip that leaned on the checkpoint two screens back would be commentary on
    how they're doing rather than teaching about the material.
    """

    slug: str
    section_index: int = Field(ge=0)
    # Tips already shown earlier in this lesson. Each screen is a separate,
    # independent call, so without these the model cheerfully makes the same
    # point six sections running.
    shown_tips: list[str] = Field(default_factory=list, max_length=12)


class CoachOut(BaseModel):
    # None is a normal, common outcome: nothing worth saying on this screen.
    message: str | None = None


class ChatTurn(BaseModel):
    """One message in a chat session, carried by the client.

    The transcript is never stored. A session is a conversation, not a
    document: it lives in the browser while the tab is open and is gone
    afterwards, so the client sends the whole thing back on every turn. What
    survives a session is what it taught us about the learner — a profile
    revision, and the cards written at the end.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


# What the reader settles before a session begins. A closed set, like the
# onboarding answers, so the brief can go straight into a prompt without being
# free text the reader wrote — and so the picker and the prompt cannot drift.
SessionTopic = Literal[
    "carb_counting",
    "insulin_action",
    "exercise",
    "highs_lows",
    "ketones_sick_days",
    "tutor_picks",
]

class SessionBriefIn(BaseModel):
    """The reader's answer to "what are we doing?", set before the tutor speaks.

    A session that opens cold has to guess the subject from the profile. Letting
    the reader say so up front costs them one tap and makes the opening turn
    about the thing they actually came for — and `tutor_picks` keeps the old
    behaviour for a reader who genuinely doesn't know, which is a real case and
    the reason the tutor speaks first at all.

    A subject and nothing else. How long the session runs is not the reader's
    to price before it starts: the tutor proposes the ending when the work is
    done, on `ChatOut.wrap_up`.
    """

    topic: SessionTopic = "tutor_picks"


class ChatStartIn(BaseModel):
    brief: SessionBriefIn = Field(default_factory=SessionBriefIn)


class ChatIn(BaseModel):
    # The conversation so far, ending with the reader's new message.
    messages: list[ChatTurn] = Field(min_length=1, max_length=80)
    # Carried on every turn, not just the first. The brief is what keeps a
    # session on the subject the reader chose — sent once, it would be forty
    # turns behind by the end and stop meaning anything.
    brief: SessionBriefIn = Field(default_factory=SessionBriefIn)


class ChatCheckOptionOut(BaseModel):
    """One answer to a tutor's check.

    `correct` and `response` go to the client, unlike a lesson checkpoint's
    answer key. There is nothing to protect here: a check has no retry and is
    never recorded, so the reader learns which one it was the moment they pick,
    and shipping the whole thing means they find out without a round trip.
    """

    text: str
    correct: bool
    response: str


class ChatCheckOut(BaseModel):
    question: str
    options: list[ChatCheckOptionOut]


class ChatOut(BaseModel):
    reply: str
    # A question the tutor stopped to ask, rendered as a quiz rather than as
    # part of the message. Null on most turns — see `app/chat.py`.
    check: ChatCheckOut | None = None
    # Set only when this turn revised the profile, so the frontend can take the
    # new copy rather than refetching to find out whether anything changed.
    # Null on almost every turn, by design.
    profile: ProfileOut | None = None
    # The tutor judging that the session has done its work. A proposal the app
    # renders as an offer to finish — the reader can carry on regardless, and
    # the next turn simply comes back false.
    wrap_up: bool = False


class ChatEndIn(BaseModel):
    messages: list[ChatTurn] = Field(default_factory=list, max_length=80)
    # Same brief the session opened with, so the recap can name the subject
    # without re-deriving it from the transcript.
    brief: SessionBriefIn = Field(default_factory=SessionBriefIn)
    # The client tallies this as checks are answered — a check is never
    # recorded anywhere else, so this is the only way the count reaches the
    # server at all.
    checks_correct: int = Field(default=0, ge=0)
    checks_total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _correct_within_total(self) -> ChatEndIn:
        if self.checks_correct > self.checks_total:
            raise ValueError("checks_correct cannot exceed checks_total")
        return self


class ChatSessionOut(BaseModel):
    """A generated recap of a finished session — never the conversation
    itself. See `app/chat_summary.py`."""

    id: str
    topic: SessionTopic
    headline: str
    summary: str
    checks_correct: int
    checks_total: int
    cards_added: int
    created_at: datetime


class ChatEndOut(BaseModel):
    """What the session left behind.

    Zero cards is the common outcome for a short conversation, and not a
    failure — see `app/chat_cards.py`. `session` is the recap this endpoint
    always writes, generated when possible and a plain fallback otherwise —
    see `app/chat_summary.py`.
    """

    cards_added: int
    session: ChatSessionOut


class ProgressUpdate(BaseModel):
    completed: bool = True
    # The full, ordered sequence of checkpoint attempts made this session.
    # Ignored unless `completed` is true — an abandoned lesson leaves nothing behind.
    answers: list[AnswerAttemptIn] = Field(default_factory=list)


class LessonProgressOut(BaseModel):
    slug: str
    completed: bool
    last_viewed_at: datetime
    completed_at: datetime | None


class CategoryStat(BaseModel):
    category: str
    total: int
    completed: int


class FlashcardOut(BaseModel):
    """One card in the review deck, capped at 20 and reader-specific.

    Three kinds:
    - `chat_gap` — written by the model at the end of a chat session, for
      something the reader visibly struggled with in the conversation. The only
      generated text in the deck, and the only card that can't be written again,
      so it's also kept permanently in its own table.
    - `weak_spot` — built from a checkpoint this reader has actually gotten
      wrong, using the question's own prompt and explanation. Nothing new is
      authored or stored; this is `question_responses` read back as review
      material, which is the reason that table keeps every attempt.
    - `takeaway` — hand-authored content: one of the twenty starter cards a new
      reader is seeded with, or a completed lesson's key takeaway. The deck's
      filler, and the first kind to give up its slot when something sharper
      arrives.

    A stored queue, not a view: added to only at the end of a lesson session and
    the end of a chat session, oldest card dropping off to make room. Reviewing
    the deck never changes it. Returned newest first.

    `lesson_slug` and `category` are empty on a `chat_gap` card, which belongs
    to no lesson; `lesson_title` carries what the conversation was about
    instead, so the deck has something to label every card with.
    """

    id: str
    kind: Literal["chat_gap", "weak_spot", "takeaway"]
    lesson_slug: str
    lesson_title: str
    category: str
    front: str
    back: str


class DeckStat(BaseModel):
    """What the review deck is made of, by where each card came from.

    The deck is always 20 cards once seeded, so the total says little on its
    own — the split is the interesting part. `starters` is the hand-written
    filler a new reader begins with, and watching it fall to zero is watching
    the deck become theirs.
    """

    total: int
    weak_spots: int
    from_tutor: int
    starters: int


class TroubleSpotOut(BaseModel):
    """A lesson this reader has missed checkpoints in, worth going back to.

    First attempts only — the retry that follows coaching isn't evidence of
    anything. Carries the slug so the dashboard can link straight back.
    """

    lesson_slug: str
    lesson_title: str
    category: str
    asked: int
    missed: int


class StatsOut(BaseModel):
    lessons_total: int
    lessons_completed: int
    percent_complete: int
    minutes_learned: int
    streak_days: int
    last_activity_at: datetime | None
    by_category: list[CategoryStat]
    next_lesson: LessonSummary | None

    # Checkpoints, counted on first attempts only.
    checkpoints_answered: int
    checkpoints_correct: int
    checkpoint_accuracy: int

    deck: DeckStat
    # Cards the tutor has written across every session; these are kept
    # permanently, so this outlives the ones still in the deck.
    tutor_cards: int
    trouble_spots: list[TroubleSpotOut]
