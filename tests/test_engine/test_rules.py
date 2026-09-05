"""Tests for ash_captions.engine.rules.

Pure logic, no I/O -- these tests build Word lists by hand and assert on
the resulting Card list directly.
"""
from __future__ import annotations

from ash_captions.engine.rules import (
    GAP_SNAP_THRESHOLD_SECONDS,
    MIN_CARD_DURATION_SECONDS,
    Card,
    build_cards,
)
from ash_captions.engine.transcribe import Word


def word(text, start, end):
    return Word(text=text, start=start, end=end)


def test_empty_input_returns_no_cards():
    assert build_cards([]) == []


def test_groups_into_three_to_four_word_cards_by_default():
    words = [word(str(i), i * 0.3, i * 0.3 + 0.25) for i in range(12)]
    cards = build_cards(words)

    assert sum(len(c.words) for c in cards) == 12
    for card in cards:
        assert 3 <= len(card.words) <= 4


def test_max_words_hard_cap_without_punctuation():
    # No punctuation anywhere: every card should hit the max, except
    # possibly a merged remainder.
    words = [word(f"w{i}", i, i + 0.9) for i in range(8)]
    cards = build_cards(words, min_words=3, max_words=4)

    assert [len(c.words) for c in cards] == [4, 4]


def test_punctuation_triggers_early_break_at_min_words():
    words = [
        word("One", 0.0, 0.2),
        word("two", 0.3, 0.5),
        word("three,", 0.6, 0.8),  # clause break, exactly at min_words (3)
        word("four", 1.0, 1.2),
        word("five", 1.3, 1.5),
        word("six", 1.6, 1.8),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 2
    assert [w.text for w in cards[0].words] == ["One", "two", "three,"]
    assert [w.text for w in cards[1].words] == ["four", "five", "six"]


def test_sentence_end_punctuation_also_triggers_early_break():
    words = [
        word("Stop", 0.0, 0.2),
        word("right", 0.3, 0.5),
        word("there.", 0.6, 0.8),
        word("Look", 1.0, 1.2),
        word("over", 1.3, 1.5),
        word("here", 1.6, 1.8),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    assert [w.text for w in cards[0].words] == ["Stop", "right", "there."]


def test_punctuation_before_min_words_does_not_break_early():
    words = [
        word("Hi,", 0.0, 0.2),  # comma at word 1, before min_words=3
        word("there", 0.3, 0.5),
        word("friend", 0.6, 0.8),
        word("welcome", 1.0, 1.2),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 1
    assert len(cards[0].words) == 4


def test_short_trailing_group_merges_into_previous_card():
    words = [word(f"w{i}", i, i + 0.9) for i in range(6)]  # groups of 4 then 2
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 1
    assert len(cards[0].words) == 6


def test_single_short_group_is_not_merged_away():
    words = [word("Hi", 0.0, 0.2), word("there", 0.3, 0.5)]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 1
    assert len(cards[0].words) == 2


def test_minimum_card_duration_is_enforced():
    words = [
        word("Hi", 0.0, 0.1),
        word("there", 0.15, 0.25),
        word("friend", 0.3, 0.35),  # card spans 0.0-0.35s, well under 0.5s floor
    ]
    cards = build_cards(words, min_words=3, max_words=4, gap_snap_threshold=0.0)

    assert len(cards) == 1
    duration = cards[0].end - cards[0].start
    assert duration >= MIN_CARD_DURATION_SECONDS


def test_minimum_duration_never_overlaps_the_next_card():
    words = [
        word("Hi", 0.0, 0.1),
        word("there", 0.15, 0.2),
        word("you,", 0.25, 0.3),  # card 1: 0.0-0.3s (short, needs extension)
        word("friend", 0.32, 0.9),  # card 2 starts right after -- little room
        word("stay", 1.0, 1.2),
        word("well", 1.3, 1.5),
    ]
    cards = build_cards(words, min_words=3, max_words=4, gap_snap_threshold=0.0)

    assert cards[0].end <= cards[1].start


def test_gap_snapping_closes_small_gaps():
    words = [
        word("Hi", 0.0, 0.1),
        word("there", 0.15, 0.2),
        word("friend.", 0.25, 0.30),
        # small 0.05s gap here, well under the default snap threshold
        word("Nice", 0.35, 0.5),
        word("day", 0.55, 0.7),
        word("out.", 0.75, 0.9),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 2
    # the gap was closed at the midpoint, so the two cards touch exactly
    assert cards[0].end == cards[1].start


def test_large_gap_is_not_snapped():
    words = [
        word("Hi", 0.0, 0.1),
        word("there", 0.15, 0.2),
        word("friend.", 0.25, 0.30),
        word("Later,", 5.0, 5.2),
        word("bye", 5.3, 5.5),
        word("now", 5.6, 5.8),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 2
    gap = cards[1].start - cards[0].end
    assert gap > GAP_SNAP_THRESHOLD_SECONDS


def test_isolated_single_word_card_is_dropped():
    words = [
        word("Real", 0.0, 0.2),
        word("speech", 0.3, 0.5),
        word("here,", 0.6, 0.8),
        word("um", 3.0, 3.1),  # isolated: >=1.5s of silence on both sides
        word("More", 6.0, 6.2),
        word("real", 6.3, 6.5),
        word("speech.", 6.6, 6.8),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    texts = [c.text for c in cards]
    assert "um" not in texts
    assert len(cards) == 2


def test_isolated_multi_word_card_is_kept():
    words = [
        word("Real", 0.0, 0.2),
        word("speech", 0.3, 0.5),
        word("here,", 0.6, 0.8),
        word("actually", 3.0, 3.2),
        word("wait", 3.3, 3.5),  # 2-word isolated group: kept, not dropped
        word("More", 6.0, 6.2),
        word("real", 6.3, 6.5),
        word("speech.", 6.6, 6.8),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    texts = " ".join(c.text for c in cards)
    assert "actually wait" in texts


def test_single_isolated_card_overall_is_not_dropped():
    # With only one card total, there's nothing to compare gaps against,
    # so it must never be dropped no matter how it looks in isolation.
    words = [word("Hi", 0.0, 0.2)]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 1
    assert cards[0].text == "Hi"


def test_isolated_word_at_start_is_dropped_if_gap_after_is_large():
    words = [
        word("um", 0.0, 0.1),
        word("Real", 3.0, 3.2),
        word("speech", 3.3, 3.5),
        word("here.", 3.6, 3.8),
    ]
    cards = build_cards(words, min_words=3, max_words=4)

    assert len(cards) == 1
    assert "um" not in cards[0].text


def test_card_text_joins_words_with_spaces():
    card = Card(words=(word("Hello", 0, 1), word("world", 1, 2)), start=0, end=2)
    assert card.text == "Hello world"


def test_silence_gap_threshold_is_configurable():
    words = [
        word("Real", 0.0, 0.2),
        word("speech", 0.3, 0.5),
        word("here,", 0.6, 0.8),
        word("um", 1.6, 1.7),  # only 0.8s gap -- not isolated under default 1.5s
        word("More", 3.4, 3.6),
        word("real", 3.7, 3.9),
        word("speech.", 4.0, 4.2),
    ]
    cards_default = build_cards(words, min_words=3, max_words=4)
    assert "um" in " ".join(c.text for c in cards_default)

    cards_strict = build_cards(words, min_words=3, max_words=4, silence_gap=0.5)
    assert "um" not in " ".join(c.text for c in cards_strict)
