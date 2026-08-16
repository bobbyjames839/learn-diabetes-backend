from app.chat import Turn
from app.chat_cards import build_user_prompt, looks_like_dosing, parse_cards


def card(front="Why does exercise cause a low hours later?", back="Muscles refill glycogen.", **kw):
    return {"front": front, "back": back, "topic": "exercise lows", **kw}


class TestParseCards:
    def test_keeps_a_good_card(self):
        cards = parse_cards({"cards": [card()]})
        assert len(cards) == 1
        assert cards[0].topic == "exercise lows"

    def test_no_cards_is_a_normal_outcome(self):
        # Most short conversations produce none.
        assert parse_cards({"cards": []}) == []

    def test_an_unusable_reply_leaves_no_cards(self):
        assert parse_cards({}) == []
        assert parse_cards({"cards": "lots"}) == []

    def test_caps_what_one_conversation_may_add(self):
        many = [card(front=f"Question {i}?") for i in range(12)]
        assert len(parse_cards({"cards": many})) == 5

    def test_drops_a_repeat_of_the_same_confusion(self):
        cards = parse_cards({"cards": [card(), card(front=card()["front"].upper())]})
        assert len(cards) == 1

    def test_drops_a_card_that_is_missing_a_side(self):
        assert parse_cards({"cards": [card(back="   ")]}) == []

    def test_drops_an_over_long_card_rather_than_the_whole_set(self):
        cards = parse_cards({"cards": [card(back="x" * 900), card(front="Why does fat delay it?")]})
        assert len(cards) == 1
        assert cards[0].front == "Why does fat delay it?"

    def test_a_dosing_card_never_lands(self):
        # The prompt forbids it; this is the backstop for when it happens anyway.
        cards = parse_cards(
            {"cards": [card(back="You should take 2 extra units of insulin for a meal like that.")]}
        )
        assert cards == []


class TestDosingScreen:
    def test_catches_an_instruction_about_an_amount(self):
        assert looks_like_dosing("You would take about 3 units to correct that.")
        assert looks_like_dosing("Increase your basal insulin overnight.")

    def test_leaves_mechanism_alone(self):
        # These are the cards the app exists to write. A screen that eats them
        # is worse than no screen.
        assert not looks_like_dosing(
            "Rapid-acting insulin takes about 15 minutes to start working, so glucose "
            "rises before it has any effect."
        )
        assert not looks_like_dosing(
            "Exercise makes muscle take up glucose without needing insulin, which is why "
            "levels can fall during a long walk."
        )
        assert not looks_like_dosing(
            "Your liver releases stored glucose overnight, which is why levels can rise "
            "before you have eaten anything."
        )


class TestPrompt:
    def test_labels_both_speakers(self):
        prompt = build_user_prompt(
            [Turn(role="user", content="why do I go low after football?"),
             Turn(role="assistant", content="Muscles keep pulling glucose in afterwards.")]
        )
        assert "LEARNER: why do I go low after football?" in prompt
        assert "YOU: Muscles keep pulling glucose in afterwards." in prompt
