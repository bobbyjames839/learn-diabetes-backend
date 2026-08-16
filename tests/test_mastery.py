"""The per-area ratings — the half of the profile the reader doesn't write.

What's pinned here is mostly the *restraint*: a rating moves towards what a
session showed rather than to it, one answer barely counts, and nothing a model
or a client sends can put a value outside 1-100 or invent an area.
"""

from typing import get_args

from app import mastery
from app.chat import LearnerProfile, describe_profile
from app.coach import build_user_prompt
from app.schemas import SessionTopic


class TestWithDefaults:
    def test_a_reader_with_no_history_sits_in_the_middle(self):
        ratings = mastery.with_defaults(None)
        assert set(ratings) == set(mastery.AREAS)
        assert set(ratings.values()) == {mastery.DEFAULT_RATING}

    def test_stored_values_win_and_the_rest_default(self):
        ratings = mastery.with_defaults({"carb_counting": 12})
        assert ratings["carb_counting"] == 12
        assert ratings["exercise"] == mastery.DEFAULT_RATING

    def test_an_area_that_no_longer_exists_is_dropped(self):
        # So the closed set can change without a migration or a bad prompt.
        assert "pancreas_trivia" not in mastery.with_defaults({"pancreas_trivia": 90})

    def test_junk_in_the_column_cannot_escape_the_scale(self):
        ratings = mastery.with_defaults(
            {"carb_counting": 5000, "exercise": -20, "insulin_action": True}
        )
        assert ratings["carb_counting"] == mastery.CEILING
        assert ratings["exercise"] == mastery.FLOOR
        # A bool is an int in Python and would silently read as 1/100.
        assert ratings["insulin_action"] == mastery.DEFAULT_RATING


class TestBlend:
    def test_no_answers_move_nothing(self):
        assert mastery.blend(50, 0, 0) == 50

    def test_getting_them_right_raises_it_and_wrong_lowers_it(self):
        assert mastery.blend(50, 4, 4) > 50
        assert mastery.blend(50, 0, 4) < 50

    def test_one_answer_is_a_nudge_not_a_verdict(self):
        # A single lucky checkpoint should barely register.
        assert mastery.blend(50, 1, 1) - 50 <= 7

    def test_no_session_moves_more_than_half_the_distance(self):
        # An afternoon of tired guessing is evidence, not a re-rating.
        assert mastery.blend(100, 0, 40) >= 50
        assert mastery.blend(1, 40, 40) <= 51

    def test_it_stays_inside_the_scale(self):
        assert mastery.blend(100, 20, 20) <= mastery.CEILING
        assert mastery.blend(1, 0, 20) >= mastery.FLOOR

    def test_a_perfect_run_converges_rather_than_jumping(self):
        rating = mastery.DEFAULT_RATING
        for _ in range(6):
            rating = mastery.blend(rating, 4, 4)
        assert 80 < rating < mastery.CEILING


class TestApplyEvidence:
    def test_the_whole_map_comes_back(self):
        ratings = mastery.apply_evidence({}, {"exercise": (3, 4)})
        assert set(ratings) == set(mastery.AREAS)

    def test_only_the_area_with_evidence_moves(self):
        ratings = mastery.apply_evidence({}, {"exercise": (0, 4)})
        assert ratings["exercise"] < mastery.DEFAULT_RATING
        assert ratings["carb_counting"] == mastery.DEFAULT_RATING

    def test_evidence_for_an_unknown_area_is_ignored(self):
        ratings = mastery.apply_evidence({}, {"astrology": (0, 4)})
        assert set(ratings) == set(mastery.AREAS)

    def test_sessions_accumulate(self):
        first = mastery.apply_evidence({}, {"carb_counting": (0, 4)})
        second = mastery.apply_evidence(first, {"carb_counting": (0, 4)})
        assert second["carb_counting"] < first["carb_counting"]


class TestMappings:
    """Evidence has to be able to reach every area, or it is a dead row."""

    def test_every_mapped_area_exists(self):
        for area in (*mastery.CATEGORY_AREAS.values(), *mastery.TOPIC_AREAS.values()):
            assert area in mastery.AREAS

    def test_every_area_is_reachable_from_somewhere(self):
        reachable = set(mastery.CATEGORY_AREAS.values()) | set(mastery.TOPIC_AREAS.values())
        assert reachable == set(mastery.AREAS)

    def test_every_session_topic_but_the_open_one_is_attributable(self):
        # `tutor_picks` names no subject on purpose — the recap names the area
        # for those sessions instead.
        topics = set(get_args(SessionTopic)) - {"tutor_picks"}
        assert topics == set(mastery.TOPIC_AREAS)


class TestDescribe:
    def test_weakest_first(self):
        text = mastery.describe({"carb_counting": 90, "exercise": 10})
        assert text.index("exercise") < text.index("counting carbohydrate")

    def test_it_reads_as_words_not_just_numbers(self):
        assert "(shaky)" in mastery.describe({"carb_counting": 10})
        assert "(strong)" in mastery.describe({"carb_counting": 95})

    def test_it_forbids_reading_the_numbers_out(self):
        # A model handed scores about the person it is talking to will open
        # with them unless told not to, and that is a report card.
        assert "Never say the numbers" in mastery.describe(None)


class TestItReachesThePrompts:
    def test_the_tutor_is_handed_them_with_the_rest_of_the_profile(self):
        prompt = describe_profile(LearnerProfile(name="Sam", area_ratings={"exercise": 20}))
        assert "WHERE THEY STAND" in prompt
        assert "exercise and activity: 20" in prompt

    def test_a_reader_with_none_yet_still_gets_a_full_map(self):
        prompt = describe_profile(LearnerProfile(name="Sam"))
        assert f"counting carbohydrate: {mastery.DEFAULT_RATING}" in prompt

    def test_the_coach_is_handed_them_too(self):
        prompt = build_user_prompt(
            "Carbs", "What counts", "Body text.", [], area_ratings={"carb_counting": 30}
        )
        assert "counting carbohydrate: 30" in prompt
