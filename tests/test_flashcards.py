from datetime import datetime, timedelta, timezone

from app.flashcards import Slot, plan_intake

EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


def at(day: int) -> datetime:
    """A timestamp. Lower is older."""
    return EPOCH + timedelta(days=day)


def card(kind: str, back: str, question_id: str | None = None) -> dict:
    return {"kind": kind, "question_id": question_id, "front": "f", "back": back}


def slot(position: int, kind: str, back: str, day: int, question_id: str | None = None) -> Slot:
    return Slot(
        position=position,
        key=(kind, question_id, "f", back),
        kind=kind,
        age=at(day),
    )


def full_deck(kind: str = "takeaway") -> list[Slot]:
    """Twenty slots, position 0 the oldest."""
    return [slot(i, kind, f"t{i}", day=i) for i in range(20)]


class TestFillingUp:
    def test_an_empty_deck_takes_the_lowest_slots_in_order(self):
        intake = plan_intake([], [card("takeaway", "a"), card("takeaway", "b")])
        assert intake.placed == [(0, card("takeaway", "a")), (1, card("takeaway", "b"))]
        assert intake.renewed == []

    def test_a_partial_deck_fills_the_gap_before_evicting(self):
        # A slot freed by nothing in particular is still the cheapest place to
        # put something: filling it costs no one their card.
        deck = [s for s in full_deck() if s.position != 7]
        intake = plan_intake(deck, [card("weak_spot", "new", question_id="q")])
        assert intake.placed == [(7, card("weak_spot", "new", question_id="q"))]

    def test_intake_never_exceeds_one_deckful(self):
        # Past that a session is only overwriting itself, and the tail of the
        # candidate list would evict the head of it.
        intake = plan_intake([], [card("takeaway", str(i)) for i in range(50)])
        assert len(intake.placed) == 20


class TestEviction:
    def test_the_oldest_card_gives_up_its_slot(self):
        intake = plan_intake(full_deck(), [card("weak_spot", "new", question_id="q")])
        assert intake.placed == [(0, card("weak_spot", "new", question_id="q"))]

    def test_the_deck_drains_oldest_first(self):
        cards = [card("weak_spot", f"w{i}", question_id=f"q{i}") for i in range(3)]
        intake = plan_intake(full_deck(), cards)
        assert [position for position, _ in intake.placed] == [0, 1, 2]

    def test_a_takeaway_yields_before_a_weak_spot_however_old(self):
        # The deck is mostly hard-won weak spots with one takeaway sitting in
        # it. The takeaway is the filler, so it goes first even though every
        # weak spot in the deck is older than it.
        deck = [slot(i, "weak_spot", f"w{i}", day=i, question_id=f"q{i}") for i in range(19)]
        deck.append(slot(19, "takeaway", "filler", day=99))
        intake = plan_intake(deck, [card("chat_gap", "gap")])
        assert intake.placed == [(19, card("chat_gap", "gap"))]

    def test_weak_spots_evict_each_other_once_the_filler_is_gone(self):
        deck = [slot(i, "weak_spot", f"w{i}", day=i, question_id=f"q{i}") for i in range(20)]
        intake = plan_intake(deck, [card("chat_gap", "gap")])
        assert intake.placed == [(0, card("chat_gap", "gap"))]

    def test_a_session_never_evicts_what_it_just_placed(self):
        cards = [card("weak_spot", f"w{i}", question_id=f"q{i}") for i in range(20)]
        intake = plan_intake(full_deck(), cards)
        assert sorted(position for position, _ in intake.placed) == list(range(20))

    def test_ties_are_broken_by_slot_so_eviction_is_deterministic(self):
        # Every slot in a freshly seeded deck is written in the same instant.
        deck = [slot(i, "takeaway", f"t{i}", day=0) for i in range(20)]
        intake = plan_intake(deck, [card("weak_spot", "new", question_id="q")])
        assert intake.placed[0][0] == 0


class TestRenewal:
    def test_a_card_already_in_the_deck_is_held_not_duplicated(self):
        deck = full_deck()
        intake = plan_intake(deck, [card("takeaway", "t5")])
        assert intake.renewed == [5]
        assert intake.placed == []

    def test_renewing_costs_nobody_their_slot(self):
        # Finishing a lesson again shouldn't quietly cost the reader three
        # cards from earlier sessions.
        intake = plan_intake(full_deck(), [card("takeaway", f"t{i}") for i in (3, 4, 5)])
        assert intake.placed == []
        assert intake.renewed == [3, 4, 5]

    def test_the_same_checkpoint_missed_twice_is_one_card(self):
        again = card("weak_spot", "explanation", question_id="q1")
        first = plan_intake(full_deck(), [again])
        assert first.placed == [(0, again)]

        deck = full_deck()
        deck[0] = slot(0, "weak_spot", "explanation", day=50, question_id="q1")
        assert plan_intake(deck, [again]).renewed == [0]

    def test_a_card_placed_this_session_is_recognised_by_the_next_candidate(self):
        duplicate = card("weak_spot", "same", question_id="q1")
        intake = plan_intake(full_deck(), [duplicate, duplicate])
        assert len(intake.placed) == 1
        assert intake.renewed == [0]


class TestIntake:
    def test_length_counts_everything_the_session_touched(self):
        intake = plan_intake(full_deck(), [card("takeaway", "t1"), card("chat_gap", "new")])
        assert len(intake) == 2

    def test_a_session_with_nothing_new_touches_nothing(self):
        assert len(plan_intake(full_deck(), [])) == 0
