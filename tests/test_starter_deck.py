"""The starting deck is authored, so what's checked here is the authoring.

These are the only twenty cards a brand new reader sees, and they go out before
anyone has answered a single question — so they have to hold up on their own.
"""

import json
import pathlib
import re

from app.chat_cards import looks_like_dosing
from app.flashcards import DECK_SIZE
from app.starter_deck import STARTER_DECK

LESSONS = json.loads((pathlib.Path(__file__).parents[2] / "docs/lessons.json").read_text())
LESSONS = LESSONS["lessons"] if isinstance(LESSONS, dict) else LESSONS
BY_SLUG = {lesson["slug"]: lesson for lesson in LESSONS}


class TestTheDeckIsWhole:
    def test_there_are_exactly_twenty(self):
        assert len(STARTER_DECK) == DECK_SIZE

    def test_every_front_is_a_different_card(self):
        # The whole reason this deck is authored rather than cut from lesson
        # takeaways: four cards sharing a front is four copies of one card.
        assert len({card.front for card in STARTER_DECK}) == DECK_SIZE

    def test_every_back_is_a_different_card(self):
        assert len({card.back for card in STARTER_DECK}) == DECK_SIZE

    def test_a_front_is_a_question_not_a_title(self):
        for card in STARTER_DECK:
            assert card.front.endswith("?"), card.front

    def test_a_front_never_just_restates_its_lesson_title(self):
        for card in STARTER_DECK:
            assert card.front.rstrip("?").strip().lower() != card.topic.lower()


class TestCoverage:
    def test_every_lesson_in_the_curriculum_is_represented(self):
        assert {card.lesson_slug for card in STARTER_DECK} == set(BY_SLUG)

    def test_every_category_is_represented(self):
        assert {card.category for card in STARTER_DECK} == {
            lesson["category"] for lesson in LESSONS
        }

    def test_no_lesson_takes_more_than_two_slots(self):
        counts: dict[str, int] = {}
        for card in STARTER_DECK:
            counts[card.lesson_slug] = counts.get(card.lesson_slug, 0) + 1
        assert max(counts.values()) <= 2


class TestTheyPointSomewhereReal:
    def test_every_card_names_a_lesson_that_exists(self):
        for card in STARTER_DECK:
            assert card.lesson_slug in BY_SLUG, card.lesson_slug

    def test_the_carried_category_matches_its_lesson(self):
        # Carried on the card as well as looked up, so a renamed slug degrades
        # to a correct-looking card rather than a blank one.
        for card in STARTER_DECK:
            assert card.category == BY_SLUG[card.lesson_slug]["category"], card.front

    def test_the_carried_topic_matches_its_lesson_title(self):
        for card in STARTER_DECK:
            assert card.topic == BY_SLUG[card.lesson_slug]["title"], card.front


class TestSafety:
    """Educational only — the same boundary every generated prompt restates,
    applied to the one set of cards a person wrote."""

    def test_no_card_reads_like_a_dosing_instruction(self):
        for card in STARTER_DECK:
            assert not looks_like_dosing(f"{card.front} {card.back}"), card.front

    def test_no_card_tells_the_reader_what_to_do(self):
        directive = re.compile(
            r"\byou (?:should|need to|must|ought to|have to)\b", re.IGNORECASE
        )
        for card in STARTER_DECK:
            assert not directive.search(f"{card.front} {card.back}"), card.front


class TestTheyFitTheCard:
    def test_a_front_fits_on_the_face(self):
        for card in STARTER_DECK:
            assert 20 <= len(card.front) <= 200, (len(card.front), card.front)

    def test_a_back_fits_on_the_face(self):
        # The deck renders at a fixed height and scrolls past roughly 500.
        for card in STARTER_DECK:
            assert 40 <= len(card.back) <= 320, (len(card.back), card.front)

    def test_nothing_has_stray_whitespace_from_being_wrapped_in_source(self):
        for card in STARTER_DECK:
            for text in (card.front, card.back):
                assert text == text.strip()
                assert "  " not in text
                assert "\n" not in text
