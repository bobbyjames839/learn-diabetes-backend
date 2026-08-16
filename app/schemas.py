from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    id: str
    email: str | None
    display_name: str | None
    diagnosed_year: int | None
    created_at: datetime


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    diagnosed_year: int | None = Field(default=None, ge=1900, le=2100)


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
    question_id: str
    chosen_index: int
    correct: bool
    correct_index: int
    explanation: str


class QuestionOut(BaseModel):
    id: str
    section_index: int
    prompt: str
    options: list[QuestionOption]
    # Present only once this reader has answered, so a revisited lesson shows
    # what they picked rather than a blank slate.
    answered: AnswerResult | None = None


class AnswerIn(BaseModel):
    chosen_index: int = Field(ge=0)


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


class ProgressUpdate(BaseModel):
    completed: bool = True


class LessonProgressOut(BaseModel):
    slug: str
    completed: bool
    last_viewed_at: datetime
    completed_at: datetime | None


class CategoryStat(BaseModel):
    category: str
    total: int
    completed: int


class StatsOut(BaseModel):
    lessons_total: int
    lessons_completed: int
    percent_complete: int
    minutes_learned: int
    streak_days: int
    last_activity_at: datetime | None
    by_category: list[CategoryStat]
    next_lesson: LessonSummary | None
