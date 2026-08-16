from typing import get_args

import app.chat as chat
from app.chat import (
    OPENING_INSTRUCTION,
    TOPIC_BRIEFS,
    SessionBrief,
    LearnerProfile,
    Turn,
    build_messages,
    build_system_prompt,
    describe_profile,
    open_session,
    parse_reply,
    respond,
)
from app.coach import PastAnswer
from app.llm import LLMNotJSON
from app.schemas import SessionTopic


class TestSystemPrompt:
    def test_carries_who_they_are(self):
        prompt = build_system_prompt(
            LearnerProfile(name="Sam", goal="newly_diagnosed", focus="exercise"), []
        )
        assert "Sam" in prompt
        assert "newly_diagnosed" in prompt
        assert "exercise" in prompt

    def test_survives_a_profile_with_nothing_in_it(self):
        # Someone can reach the chat before answering much about themselves.
        assert describe_profile(LearnerProfile())

    def test_past_answers_are_marked_as_background(self):
        prompt = build_system_prompt(
            LearnerProfile(),
            [
                PastAnswer(
                    concept="insulin-onset",
                    question="When does rapid-acting insulin start working?",
                    correct=False,
                    misconception="thinks it acts instantly",
                )
            ],
        )
        assert "thinks it acts instantly" in prompt
        assert "Never read it back" in prompt

    def test_says_so_when_there_is_no_history(self):
        assert "none yet" in build_system_prompt(LearnerProfile(), [])

    def test_restates_the_dosing_boundary(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "correction factor" in prompt
        assert "MECHANISM only" in prompt

    def test_offers_only_valid_profile_values(self):
        # The values come from the same Literals the onboarding quiz validates
        # against, so the prompt can't drift from the schema.
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "quick_bites | deep_dives | mixed" in prompt
        assert "why_first | examples_first" in prompt


class TestTranscript:
    def test_keeps_the_most_recent_turns(self):
        long = [Turn(role="user", content=str(i)) for i in range(200)]
        messages = build_messages(long)
        assert len(messages) == 40
        # Trimmed from the front: the end of the conversation is what matters.
        assert messages[-1]["content"] == "199"

    def test_short_conversations_pass_through_whole(self):
        assert build_messages([Turn(role="user", content="hi")]) == [
            {"role": "user", "content": "hi"}
        ]


class TestParseReply:
    def test_plain_reply(self):
        parsed = parse_reply(
            {"reply": "Glucose rises because...", "profile_update": None}
        )
        assert parsed is not None
        assert parsed.reply == "Glucose rises because..."
        assert parsed.profile_update is None

    def test_accepts_a_valid_profile_update(self):
        parsed = parse_reply(
            {"reply": "Sure.", "profile_update": {"content_preference": "why_first"}}
        )
        assert parsed is not None
        assert parsed.profile_update is not None
        assert parsed.profile_update.fields() == {"content_preference": "why_first"}

    def test_a_bad_update_is_dropped_but_the_reply_survives(self):
        # The reader asked a question. A model inventing a profile value must
        # not cost them the answer.
        parsed = parse_reply(
            {"reply": "Here's why.", "profile_update": {"focus": "vibes"}}
        )
        assert parsed is not None
        assert parsed.reply == "Here's why."
        assert parsed.profile_update is None

    def test_an_update_naming_no_fields_is_no_update(self):
        parsed = parse_reply({"reply": "Hello.", "profile_update": {}})
        assert parsed is not None
        assert parsed.profile_update is None

    def test_unknown_fields_cannot_be_written(self):
        parsed = parse_reply(
            {"reply": "Hello.", "profile_update": {"display_name": "Administrator"}}
        )
        assert parsed is not None
        assert parsed.profile_update is None

    def test_the_reply_reaches_the_reader_as_plain_prose(self):
        # Both things a real turn arrived with: provider scaffolding on the end
        # and markdown emphasis in a page that renders none.
        parsed = parse_reply(
            {
                "reply": "You said *just* the potato.\n\n"
                "<budget:token_budget>999481</budget:token_budget>"
            }
        )
        assert parsed is not None
        assert parsed.reply == "You said just the potato."

    def test_a_check_is_cleaned_too(self):
        parsed = parse_reply(
            {
                "reply": "Have a go.",
                "check": _check(question="What happens to *glucose*?"),
            }
        )
        assert parsed is not None
        assert parsed.check is not None
        assert parsed.check.question == "What happens to glucose?"

    def test_an_empty_reply_is_unusable(self):
        assert parse_reply({"reply": "   "}) is None
        assert parse_reply({}) is None
        assert parse_reply({"reply": None}) is None


class TestWrapUp:
    """The tutor proposing the end, since nobody set a length up front."""

    def test_a_turn_does_not_propose_the_end_by_default(self):
        parsed = parse_reply({"reply": "Here's why."})
        assert parsed is not None
        assert parsed.wrap_up is False

    def test_the_tutor_can_propose_the_end(self):
        parsed = parse_reply({"reply": "That's the thing worth taking away.", "wrap_up": True})
        assert parsed is not None
        assert parsed.wrap_up is True

    def test_a_turn_that_sets_another_task_is_not_an_ending(self):
        # Whatever it claims: the check is a new task, so the session is
        # visibly still going and the offer to finish would contradict it.
        parsed = parse_reply({"reply": "One more.", "wrap_up": True, "check": _check()})
        assert parsed is not None
        assert parsed.check is not None
        assert parsed.wrap_up is False

    def test_a_malformed_wrap_up_costs_nothing_else(self):
        parsed = parse_reply(
            {
                "reply": "Here's why.",
                "wrap_up": "sort of",
                "profile_update": {"content_preference": "why_first"},
            }
        )
        assert parsed is not None
        assert parsed.wrap_up is False
        assert parsed.profile_update is not None

    def test_the_prompt_makes_the_ending_the_tutors_call(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "this is your call, not theirs" in prompt

    def test_the_opening_turn_cannot_end_the_session(self):
        assert '"wrap_up" to false' in OPENING_INSTRUCTION


class TestOpening:
    """The tutor speaks first, so the opening turn is its own instruction."""

    def test_tells_the_model_to_open_rather_than_ask(self):
        assert "Open it yourself" in OPENING_INSTRUCTION
        assert "you decide" in OPENING_INSTRUCTION

    def test_defers_to_the_brief_for_the_subject(self):
        # The reader may or may not have named one, so the opening turn is told
        # where to look rather than told there isn't one.
        assert "THIS SESSION above says what they asked for" in OPENING_INSTRUCTION
        assert "if they did not, you choose the subject" in OPENING_INSTRUCTION

    def test_the_opening_sets_a_task_rather_than_explaining(self):
        # The first turn is where teach-first is most tempting and most wrong:
        # nothing is known yet about what they already think.
        assert "Do NOT explain the mechanism yet" in OPENING_INSTRUCTION
        assert "hand it straight to them to work" in OPENING_INSTRUCTION

    def test_forbids_reading_their_record_back_to_them(self):
        assert "Never mention their scores" in OPENING_INSTRUCTION

    def test_asks_for_no_profile_revision(self):
        # Nothing has been learned about the reader yet — the first message is
        # written before they have said a word.
        assert '"profile_update" and "check" to null' in OPENING_INSTRUCTION

    def test_the_opening_is_a_normal_reply_to_parse(self):
        parsed = parse_reply(
            {"reply": "Morning, Sam. Let's talk about why...", "profile_update": None}
        )
        assert parsed is not None
        assert parsed.reply.startswith("Morning, Sam.")


def _check(**overrides) -> dict:
    """A well-formed check, for tests that want to break exactly one thing."""
    return {
        "question": "Sam goes for a walk two hours after eating. What happens to glucose?",
        "options": [
            {
                "text": "It tends to fall",
                "correct": True,
                "response": "Right — muscle takes up glucose.",
            },
            {
                "text": "It stays flat",
                "correct": False,
                "response": "That assumes exercise is neutral.",
            },
        ],
        **overrides,
    }


class TestChecks:
    def test_a_well_formed_check_survives(self):
        parsed = parse_reply({"reply": "Here's why.", "check": _check()})
        assert parsed and parsed.check
        assert len(parsed.check.options) == 2
        assert sum(o.correct for o in parsed.check.options) == 1

    def test_absent_check_is_the_normal_case(self):
        parsed = parse_reply({"reply": "Just talking."})
        assert parsed and parsed.check is None

    def test_two_right_answers_is_not_a_check(self):
        options = _check()["options"]
        options[1]["correct"] = True
        parsed = parse_reply({"reply": "Here's why.", "check": _check(options=options)})
        # The reply survives; only the malformed check is shed.
        assert parsed and parsed.reply == "Here's why."
        assert parsed.check is None

    def test_no_right_answer_is_not_a_check(self):
        options = _check()["options"]
        options[0]["correct"] = False
        parsed = parse_reply({"reply": "Here's why.", "check": _check(options=options)})
        assert parsed and parsed.check is None

    def test_one_option_is_not_a_question(self):
        parsed = parse_reply(
            {"reply": "Here's why.", "check": _check(options=_check()["options"][:1])}
        )
        assert parsed and parsed.check is None

    def test_a_dosing_question_is_dropped(self):
        # The shape the prompt forbids: a quiz asking them to pick an action.
        # The regex is narrow on purpose (see app/safety.py) — the prompt is the
        # main defence, and this only has to catch the unmistakable phrasings.
        parsed = parse_reply(
            {
                "reply": "Here's why.",
                "check": _check(
                    question="How much insulin would you take to correct that?"
                ),
            }
        )
        assert parsed and parsed.reply == "Here's why."
        assert parsed.check is None

    def test_dosing_in_an_option_response_is_dropped(self):
        options = _check()["options"]
        options[0]["response"] = "You would increase your basal insulin overnight."
        parsed = parse_reply({"reply": "Here's why.", "check": _check(options=options)})
        assert parsed and parsed.check is None

    def test_a_bad_check_does_not_cost_the_profile_update(self):
        parsed = parse_reply(
            {
                "reply": "Here's why.",
                "check": {"question": "", "options": []},
                "profile_update": {"content_preference": "why_first"},
            }
        )
        assert parsed and parsed.check is None
        assert parsed.profile_update and parsed.profile_update.fields() == {
            "content_preference": "why_first"
        }

    def test_the_prompt_explains_when_to_ask_one(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert '"check"' in prompt
        assert "clean choice between" in prompt

    def test_the_check_is_the_default_task_not_an_occasional_one(self):
        # The reader asked for exam-style practice, not just a chat — pin the
        # bias toward checks so a later prompt edit can't quietly undo it.
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "This is your DEFAULT task, not an occasional one" in prompt
        assert "would not have come to a tutor" in prompt

    def test_a_check_comes_before_the_explanation(self):
        # The inversion the whole session is built on: a check is a task, not a
        # test of something already delivered.
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "it comes BEFORE the explanation, not after it" in prompt

    def test_the_opening_never_carries_one(self):
        # Nothing to check before anything has been taught.
        assert '"check" to null' in OPENING_INSTRUCTION


class TestSessionBrief:
    """What the reader settles before the tutor speaks."""

    def test_a_named_topic_becomes_the_subject(self):
        brief = SessionBrief(topic="exercise")
        text = brief.describe()
        assert "why different kinds of activity move glucose" in text
        assert "do not ask what they want to cover" in text

    def test_no_topic_hands_the_choice_back_to_the_tutor(self):
        # The case the whole section was built around: a learner who doesn't
        # know what to ask for still gets taught something.
        text = SessionBrief(topic="tutor_picks").describe()
        assert "choosing one is your job" in text

    def test_the_brief_never_sets_a_length(self):
        # How long a session runs is the tutor's call, made as it goes — asking
        # the reader up front asks them to price something they haven't seen.
        text = SessionBrief(topic="exercise").describe()
        for word in ("quick", "short", "exchanges", "rounds"):
            assert word not in text.lower()

    def test_every_offerable_topic_has_a_brief(self):
        # The picker and the prompt share one closed set. A topic the frontend
        # can offer but the prompt can't describe would open on nothing.
        offerable = set(get_args(SessionTopic)) - {"tutor_picks"}
        assert offerable == set(TOPIC_BRIEFS)


class TestBriefInThePrompt:
    def test_the_brief_reaches_the_system_prompt(self):
        prompt = build_system_prompt(
            LearnerProfile(), [], SessionBrief(topic="carb_counting")
        )
        assert "counting carbohydrate" in prompt

    def test_a_session_with_no_brief_still_builds(self):
        # `respond` is called without one in tests and by any older client.
        assert "choosing one is your job" in build_system_prompt(LearnerProfile(), [])


class TestTeachingMethod:
    """The session asks first and explains second.

    A tutor that explains and then tests is a lesson read aloud, and the reader
    already has lessons. These pin the inversion, because it is the one thing a
    later prompt edit would quietly undo.
    """

    def test_the_loop_is_stated_explicitly(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "SET A TASK -> THEY ATTEMPT IT -> RESPOND TO WHAT THEY ACTUALLY" in prompt
        assert "You do NOT explain first and test afterwards" in prompt

    def test_every_turn_ends_with_something_to_do(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "should end by handing them something to do" in prompt

    def test_the_lecture_with_a_question_mark_is_named_and_banned(self):
        # The specific failure mode: explain, then "does that make sense?".
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "That is a lecture with a question mark on it" in prompt

    def test_feedback_leads_with_what_they_got_right(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "Say what they got RIGHT first" in prompt

    def test_the_mechanism_is_taught_after_the_attempt(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "THEN give them the mechanism" in prompt
        assert "while they are holding the question" in prompt

    def test_not_knowing_is_a_legitimate_move(self):
        prompt = build_system_prompt(LearnerProfile(), [])
        assert "smaller version of the same task rather than the answer" in prompt

    def test_one_task_at_a_time(self):
        assert "Do not stack two tasks in one turn" in build_system_prompt(LearnerProfile(), [])


class TestJSONFallback:
    """Claude occasionally answers in full but skips the JSON wrapper on this
    session's long, conversational system prompt. `complete_json_chat` raises
    `LLMNotJSON` for that case rather than the generic `LLMError`, and the
    session should still show the reader the answer instead of a 503 — the
    wrapper is a mechanism, not the thing they were waiting for.
    """

    def test_a_wrapperless_opening_still_greets_them(self, monkeypatch):
        def fake(*args, **kwargs):
            raise LLMNotJSON("nope", "Right, let's look at porridge vs an apple.")

        monkeypatch.setattr(chat, "complete_json_chat", fake)
        assert open_session(LearnerProfile(), []) == "Right, let's look at porridge vs an apple."

    def test_a_wrapperless_turn_still_answers(self, monkeypatch):
        def fake(*args, **kwargs):
            raise LLMNotJSON("nope", "You spotted the carb — here's why that matters.")

        monkeypatch.setattr(chat, "complete_json_chat", fake)
        reply = respond(LearnerProfile(), [], [Turn(role="user", content="an apple")])
        assert reply is not None
        assert reply.reply == "You spotted the carb — here's why that matters."
        assert reply.check is None
        assert reply.profile_update is None

    def test_an_empty_fallback_is_still_unusable(self, monkeypatch):
        monkeypatch.setattr(
            chat, "complete_json_chat", lambda *a, **k: (_ for _ in ()).throw(LLMNotJSON("nope", "   "))
        )
        assert open_session(LearnerProfile(), []) is None

    def test_a_fallback_that_reads_as_dosing_is_dropped(self, monkeypatch):
        dosing = "You should take about 3 units to cover that."
        monkeypatch.setattr(
            chat, "complete_json_chat", lambda *a, **k: (_ for _ in ()).throw(LLMNotJSON("nope", dosing))
        )
        assert open_session(LearnerProfile(), []) is None
        assert respond(LearnerProfile(), [], [Turn(role="user", content="hi")]) is None
