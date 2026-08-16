from app.learner import (
    LearnerEvidence,
    inferred_focus,
    next_experience,
    profile_revisions,
)


def evidence(lessons=0, asked=0, correct=0, wrong=None) -> LearnerEvidence:
    return LearnerEvidence(
        lessons_completed=lessons,
        questions_asked=asked,
        questions_correct=correct,
        wrong_by_category=wrong or {},
    )


class TestExperience:
    def test_promotes_once_enough_lessons_are_done_well(self):
        assert next_experience("new", evidence(lessons=3, asked=10, correct=8)) == "basics"

    def test_volume_alone_is_not_experience(self):
        # Twelve lessons finished, half the questions wrong: clicking through.
        assert next_experience("new", evidence(lessons=12, asked=20, correct=10)) is None

    def test_reaches_experienced_and_stops(self):
        strong = evidence(lessons=12, asked=40, correct=36)
        assert next_experience("basics", strong) == "experienced"
        assert next_experience("experienced", strong) is None

    def test_never_demotes_after_a_bad_run(self):
        assert next_experience("experienced", evidence(lessons=1, asked=10, correct=1)) is None

    def test_leaves_an_unrecognised_value_alone(self):
        assert next_experience("expert", evidence(lessons=20, asked=40, correct=40)) is None

    def test_no_answers_is_not_a_promotion(self):
        assert next_experience("new", evidence(lessons=5)) is None


class TestFocus:
    def test_fills_in_the_area_they_miss_most(self):
        found = inferred_focus("not_sure", evidence(wrong={"insulin": 5, "food": 1}))
        assert found == "insulin_action"

    def test_leaves_a_stated_focus_alone(self):
        # Focus asks what they *want* to understand. Being bad at insulin is
        # not the same as wanting to study it.
        assert inferred_focus("exercise", evidence(wrong={"insulin": 9})) is None

    def test_one_bad_question_is_not_an_area(self):
        assert inferred_focus("not_sure", evidence(wrong={"insulin": 1})) is None

    def test_ignores_categories_with_no_focus_of_their_own(self):
        assert inferred_focus("not_sure", evidence(wrong={"basics": 8})) is None

    def test_ties_break_stably(self):
        tied = evidence(wrong={"insulin": 4, "food": 4})
        assert inferred_focus("not_sure", tied) == inferred_focus("not_sure", tied)


class TestRevisions:
    def test_usually_nothing_changes(self):
        assert profile_revisions("basics", "exercise", evidence(lessons=4, asked=8, correct=7)) == {}

    def test_both_halves_can_move_at_once(self):
        revisions = profile_revisions(
            "new",
            "not_sure",
            evidence(lessons=4, asked=20, correct=14, wrong={"food": 4}),
        )
        assert revisions == {
            "onboarding_experience": "basics",
            "onboarding_focus": "carb_counting",
        }

    def test_a_reader_who_never_answered_onboarding_is_left_alone(self):
        assert profile_revisions(None, None, evidence(lessons=20, asked=40, correct=40)) == {}
