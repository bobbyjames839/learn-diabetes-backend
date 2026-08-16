from app.chat import Turn
from app.chat_summary import build_user_prompt, fallback, parse_summary, topic_label


def summary(**kw):
    return {
        "headline": "Carb counting with mixed meals",
        "summary": "You worked through why fat and protein slow a meal down, and it landed well.",
        **kw,
    }


class TestParseSummary:
    def test_keeps_a_good_summary(self):
        parsed = parse_summary(summary())
        assert parsed is not None
        assert parsed.headline == "Carb counting with mixed meals"

    def test_an_unusable_reply_falls_back_to_none(self):
        assert parse_summary({}) is None
        assert parse_summary({"headline": "x"}) is None

    def test_a_dosing_summary_never_lands(self):
        # Same narrow, sentence-scoped check `chat_cards.py` runs on a card's
        # front/back — see app/safety.py for why it doesn't also catch a bare
        # interrogative phrase like a check question is allowed to.
        assert parse_summary(summary(summary="You should take 2 extra units for a meal like that.")) is None
        assert parse_summary(summary(headline="Increase your basal insulin overnight")) is None

    def test_strips_whitespace(self):
        parsed = parse_summary(summary(headline="  Exercise and lows  "))
        assert parsed is not None
        assert parsed.headline == "Exercise and lows"


class TestPrompt:
    def test_labels_both_speakers_and_the_subject(self):
        prompt = build_user_prompt(
            [
                Turn(role="user", content="why do I go low after football?"),
                Turn(role="assistant", content="Muscles keep pulling glucose in afterwards."),
            ],
            "exercise",
        )
        assert "SESSION SUBJECT: exercise" in prompt
        assert "LEARNER: why do I go low after football?" in prompt
        assert "YOU: Muscles keep pulling glucose in afterwards." in prompt


class TestTopicLabel:
    def test_known_topics_get_a_natural_phrase(self):
        assert topic_label("carb_counting") == "counting carbs"
        assert topic_label("exercise") == "exercise"

    def test_tutor_picks_has_no_named_subject(self):
        assert topic_label("tutor_picks") == "a tutor session"


class TestFallback:
    def test_never_fails_and_names_the_topic(self):
        recap = fallback("insulin_action", 0, 0)
        assert "how insulin acts" in recap.summary
        assert recap.headline

    def test_includes_the_check_tally_when_there_is_one(self):
        recap = fallback("exercise", 2, 3)
        assert "2 of 3 checks answered correctly" in recap.summary

    def test_omits_the_tally_when_no_checks_were_answered(self):
        recap = fallback("exercise", 0, 0)
        assert "checks answered" not in recap.summary

    def test_an_unnamed_topic_still_reads_naturally(self):
        recap = fallback("tutor_picks", 0, 0)
        assert recap.headline == "Tutor session"
        assert "a tutor session" in recap.summary
