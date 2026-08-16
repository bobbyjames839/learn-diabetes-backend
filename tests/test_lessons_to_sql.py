import pytest
from pydantic import ValidationError

from app.lessons_to_sql import LessonFile, q, to_sql

VALID = {
    "slug": "insulin-on-board",
    "title": "Insulin on board",
    "summary": "Why an earlier dose is still working.",
    "category": "insulin",
    "difficulty": 3,
    "estimated_minutes": 7,
    "key_takeaways": ["Rapid insulin keeps working for hours."],
    "body": "## Why it matters\n\nText.",
}


def lesson(**overrides):
    return {**VALID, **overrides}


def test_quotes_are_doubled_not_stripped():
    assert q("O'Brien's") == "'O''Brien''s'"


def test_apostrophes_survive_into_sql():
    parsed = LessonFile.model_validate(
        {"lessons": [lesson(body="It's Maya's liver", title="Don't panic")]}
    )
    sql = to_sql(parsed)
    assert "It''s Maya''s liver" in sql
    assert "Don''t panic" in sql


def test_order_index_follows_array_order():
    parsed = LessonFile.model_validate(
        {"lessons": [lesson(slug="first"), lesson(slug="second")]}
    )
    sql = to_sql(parsed)
    assert sql.index("'first'") < sql.index("'second'")
    assert ", 0, TRUE)" in sql and ", 1, TRUE)" in sql


def test_upsert_on_slug_so_reruns_are_safe():
    sql = to_sql(LessonFile.model_validate({"lessons": [lesson()]}))
    assert "ON CONFLICT (slug) DO UPDATE SET" in sql


@pytest.mark.parametrize(
    "override",
    [
        {"slug": "Not Kebab"},
        {"slug": "trailing-"},
        {"category": "not-a-category"},
        {"difficulty": 0},
        {"difficulty": 5},
        {"estimated_minutes": 0},
        {"estimated_minutes": 61},
        {"key_takeaways": []},
        {"body": ""},
        {"title": ""},
    ],
)
def test_invalid_lessons_are_rejected(override):
    with pytest.raises(ValidationError):
        LessonFile.model_validate({"lessons": [lesson(**override)]})


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        LessonFile.model_validate({"lessons": [lesson(author="claude")]})


def test_duplicate_slugs_are_rejected():
    with pytest.raises(ValidationError, match="duplicate slug"):
        LessonFile.model_validate({"lessons": [lesson(), lesson()]})


def test_empty_file_is_rejected():
    with pytest.raises(ValidationError):
        LessonFile.model_validate({"lessons": []})
