import random

import pytest

from app.question_gen import build_user_prompt, parse_questions, shuffle_options
from app.sections import Section


def _question(section_index=0, *, correct_count=1, misconceptions=True, coaching=True):
    options = [
        {"text": "right", "correct": True, "misconception": None, "coaching": None},
        {
            "text": "wrong a",
            "correct": False,
            "misconception": "thinks the liver stops overnight" if misconceptions else None,
            "coaching": "You're picturing the liver as idle between meals." if coaching else None,
        },
        {
            "text": "wrong b",
            "correct": False,
            "misconception": "confuses basal with bolus" if misconceptions else None,
            "coaching": "You've merged two jobs insulin does into one." if coaching else None,
        },
    ]
    if correct_count == 0:
        options[0]["correct"] = False
        options[0]["misconception"] = "also wrong"
        options[0]["coaching"] = "Not that either."
    elif correct_count > 1:
        options[1]["correct"] = True

    return {
        "section_index": section_index,
        "concept": "liver-glucose-output",
        "prompt": "Why does the liver release glucose overnight?",
        "explanation": "Your brain needs a steady supply between meals.",
        "options": options,
    }


def test_parses_a_well_formed_set():
    questions = parse_questions({"questions": [_question(0), _question(1)]}, section_count=2)

    assert [q.section_index for q in questions] == [0, 1]
    assert questions[0].concept == "liver-glucose-output"


def test_drops_questions_for_sections_that_do_not_exist():
    questions = parse_questions({"questions": [_question(0), _question(9)]}, section_count=2)

    assert [q.section_index for q in questions] == [0]


def test_keeps_only_the_first_question_per_section():
    duplicate = _question(0)
    duplicate["prompt"] = "second question for the same section"

    questions = parse_questions({"questions": [_question(0), duplicate]}, section_count=1)

    assert len(questions) == 1
    assert questions[0].prompt.startswith("Why does")


def test_rejects_a_question_with_no_correct_option():
    with pytest.raises(ValueError):
        parse_questions({"questions": [_question(correct_count=0)]}, section_count=1)


def test_rejects_a_question_with_two_correct_options():
    with pytest.raises(ValueError):
        parse_questions({"questions": [_question(correct_count=2)]}, section_count=1)


def test_rejects_a_wrong_option_with_no_misconception():
    # The misconception is the whole diagnostic point — a set without them is
    # not worth storing.
    with pytest.raises(ValueError):
        parse_questions({"questions": [_question(misconceptions=False)]}, section_count=1)


def test_rejects_a_wrong_option_with_no_coaching():
    # A wrong answer earns a retry, and a retry with nothing said in between is
    # just another guess. The coaching is what makes it a lesson.
    with pytest.raises(ValueError):
        parse_questions({"questions": [_question(coaching=False)]}, section_count=1)


def test_correct_option_never_carries_coaching():
    payload = _question(0)
    payload["options"][0]["coaching"] = "leaked from the wrong branch"

    assert parse_questions({"questions": [payload]}, 1)[0].options[0].coaching is None


def test_wrong_options_keep_their_coaching():
    question = parse_questions({"questions": [_question(0)]}, 1)[0]

    assert [o.coaching for o in question.options[1:]] == [
        "You're picturing the liver as idle between meals.",
        "You've merged two jobs insulin does into one.",
    ]


def test_rejects_a_set_with_nothing_usable():
    with pytest.raises(ValueError):
        parse_questions({"questions": [_question(5)]}, section_count=2)


def test_concept_is_normalised_to_kebab_case():
    payload = _question(0)
    payload["concept"] = "Liver Glucose Output"

    assert parse_questions({"questions": [payload]}, 1)[0].concept == "liver-glucose-output"


def test_correct_option_never_carries_a_misconception():
    payload = _question(0)
    payload["options"][0]["misconception"] = "leaked from the wrong branch"

    assert parse_questions({"questions": [payload]}, 1)[0].options[0].misconception is None


def test_prompt_numbers_every_section_and_states_the_count():
    prompt = build_user_prompt(
        "What is glucose?",
        "The fuel your body runs on.",
        [Section(0, "One", "alpha"), Section(1, "Two", "beta")],
    )

    assert "SECTION 0: One" in prompt
    assert "SECTION 1: Two" in prompt
    assert "exactly 2 questions" in prompt
    assert "section_index values 0 to 1" in prompt


def test_shuffle_does_not_always_leave_the_correct_option_first():
    # The model's raw output puts the correct option first virtually every
    # time. Across many questions the shuffle must break that pattern, not
    # just be "technically random" while landing on index 0 by chance.
    random.seed(0)
    questions = [parse_questions({"questions": [_question(0)]}, 1)[0] for _ in range(50)]

    shuffled = shuffle_options(questions)

    first_positions = [q.options.index(next(o for o in q.options if o.correct)) for q in shuffled]
    assert len(set(first_positions)) > 1
    assert not all(pos == 0 for pos in first_positions)


def test_shuffle_keeps_every_option_and_its_pairing_intact():
    question = parse_questions({"questions": [_question(0)]}, 1)[0]
    before = {(o.text, o.correct, o.misconception) for o in question.options}

    shuffled = shuffle_options([question])[0]

    after = {(o.text, o.correct, o.misconception) for o in shuffled.options}
    assert before == after


def test_prompt_labels_an_untitled_leading_section():
    prompt = build_user_prompt("T", "S", [Section(0, "", "intro")])

    assert "SECTION 0: (introduction)" in prompt
