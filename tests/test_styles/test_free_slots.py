"""``render_free.assign_slots`` -- the pure function the reel look rests on.

Nobody hand-places seven hundred words: the look lays them out and the
editor drags what landed badly (design 2026-09-05, section 5). So the
assignment is tested here, hard and without a renderer, before anything
is drawn: same phrase in, same layout out, every time.
"""
from __future__ import annotations

import pytest

from ash_captions.engine.transcribe import Word
from ash_captions.styles.render_free import assign_slots, is_connector, normalise_word
from ash_captions.styles.schema import Slot


def _words(*texts: str) -> tuple[Word, ...]:
    return tuple(Word(text=text, start=float(i), end=float(i) + 0.5) for i, text in enumerate(texts))


def _slots(*scales: float) -> tuple[Slot, ...]:
    """Slots differing only in scale, at distinct points -- the scale is
    all ``assign_slots`` reads."""
    return tuple(
        Slot(x=0.1 + 0.1 * i, y=0.2 + 0.1 * i, scale=scale) for i, scale in enumerate(scales)
    )


# ---------------------------------------------------------------------------
# the reference frame
# ---------------------------------------------------------------------------


REEL_SLOTS = _slots(0.55, 1.70, 1.10, 0.60)  # REEL ESTATE's four treatments


def test_the_reference_phrase_lands_where_the_reference_frame_has_it():
    """"the 2nd Highest residential": the huge slot takes "2nd", the next
    "Highest", the next "residential", and the small italic one "the"."""
    words = _words("the", "2nd", "Highest", "residential")
    assignment = assign_slots(words, REEL_SLOTS)
    biggest = assignment[1]
    assert REEL_SLOTS[biggest].scale == 1.70
    assert REEL_SLOTS[assignment[2]].scale == 1.10
    assert REEL_SLOTS[assignment[3]].scale == 0.60
    assert REEL_SLOTS[assignment[0]].scale == 0.55  # the connector sank


def test_every_word_gets_a_distinct_slot():
    assignment = assign_slots(_words("the", "2nd", "Highest", "residential"), REEL_SLOTS)
    assert sorted(assignment) == [0, 1, 2, 3]


def test_the_same_phrase_always_lays_out_the_same_way():
    words = _words("on", "a", "quiet", "street")
    first = assign_slots(words, REEL_SLOTS)
    assert all(assign_slots(words, REEL_SLOTS) == first for _ in range(5))


# ---------------------------------------------------------------------------
# connectors sink, content rises
# ---------------------------------------------------------------------------


def test_connectors_take_the_smallest_slots_and_content_the_biggest():
    words = _words("in", "the", "kitchen", "renovation")
    assignment = assign_slots(words, REEL_SLOTS)
    connector_scales = sorted(REEL_SLOTS[assignment[i]].scale for i in (0, 1))
    content_scales = sorted(REEL_SLOTS[assignment[i]].scale for i in (2, 3))
    assert max(connector_scales) < min(content_scales)


def test_content_words_keep_their_spoken_order_across_the_prominent_slots():
    words = _words("kitchen", "renovation", "the")
    assignment = assign_slots(words, _slots(0.5, 1.8, 1.1))
    assert assignment[0] == 1  # first content word -> biggest slot
    assert assignment[1] == 2  # second content word -> next biggest
    assert assignment[2] == 0  # the connector -> smallest


@pytest.mark.parametrize("text", ["2nd", "3", "$1.2m", "24/7", "90s"])
def test_a_token_carrying_a_digit_is_never_a_connector(text):
    assert is_connector(text) is False


def test_a_number_beats_a_connector_for_the_big_slot():
    words = _words("the", "2nd")
    assignment = assign_slots(words, _slots(0.5, 1.9))
    assert assignment[1] == 1  # "2nd" on the 1.9 slot
    assert assignment[0] == 0


@pytest.mark.parametrize(
    "text,expected",
    [
        ("the", True), ("The", True), ("The,", True), ('"the"', True), ("THE.", True),
        ("on", True), ("a", True), ("and", True), ("in", True), ("of", True),
        ("Westbrook's", False), ("residential", False), ("Highest", False),
        ("", False), ("...", False),
    ],
)
def test_classification_ignores_case_and_edge_punctuation(text, expected):
    assert is_connector(text) is expected


def test_normalise_keeps_an_internal_apostrophe():
    assert normalise_word("Westbrook's,") == "westbrook's"
    assert normalise_word("  THE.  ") == "the"


# ---------------------------------------------------------------------------
# totality and ties
# ---------------------------------------------------------------------------


def test_ties_in_scale_resolve_by_declaration_order():
    words = _words("kitchen", "renovation")
    assignment = assign_slots(words, _slots(1.0, 1.0))
    assert assignment == (0, 1)


def test_a_card_shorter_than_the_slot_list_uses_the_first_n_slots():
    assignment = assign_slots(_words("the", "kitchen"), REEL_SLOTS)
    assert set(assignment) <= {0, 1}


def test_an_all_connector_card_is_still_total():
    assignment = assign_slots(_words("the", "and", "of"), _slots(0.5, 1.8, 1.1))
    assert sorted(assignment) == [0, 1, 2]


def test_an_all_content_card_is_still_total():
    assignment = assign_slots(_words("kitchen", "renovation", "budget"), _slots(0.5, 1.8, 1.1))
    assert sorted(assignment) == [0, 1, 2]


def test_more_words_than_slots_cycles_rather_than_failing():
    # A validated look can never be here (schema requires
    # len(slots) >= max_words), but the function stays total.
    assignment = assign_slots(_words("a", "b", "c", "d", "e"), _slots(1.0, 2.0))
    assert assignment == (0, 1, 0, 1, 0)


def test_no_words_and_no_slots_are_both_empty():
    assert assign_slots((), REEL_SLOTS) == ()
    assert assign_slots(_words("kitchen"), ()) == ()
