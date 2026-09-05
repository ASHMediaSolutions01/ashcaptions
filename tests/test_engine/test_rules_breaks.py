"""``build_cards(..., breaks=CardBreaks(...))`` -- the editor's own line
breaks (v0.6 section 1). The whole point of the parameter is that it
changes nothing at all when nobody has moved a break, so that is the first
thing tested; ``test_rules.py`` next door is the rest of the proof."""
from __future__ import annotations

import random

import pytest

from ash_captions.engine.rules import CardBreaks, build_cards
from ash_captions.engine.transcribe import Word

PHRASE = "take the tool the big one and put it on the bench before it rains again today".split()


def words(texts=PHRASE, *, step=0.4):
    return [Word(text, i * step, i * step + step * 0.8, 0.9) for i, text in enumerate(texts)]


def shapes(cards):
    return [[w.text for w in card.words] for card in cards]


class TestNothingChangesWithoutMarkers:
    @pytest.mark.parametrize("max_words", [1, 2, 3, 4, 6, 8])
    def test_none_and_an_empty_set_are_the_old_behaviour(self, max_words):
        source = words()
        baseline = build_cards(source, max_words=max_words, min_words=min(3, max_words))
        for breaks in (None, CardBreaks()):
            same = build_cards(source, max_words=max_words, min_words=min(3, max_words), breaks=breaks)
            assert shapes(same) == shapes(baseline)
            assert [(c.start, c.end) for c in same] == [(c.start, c.end) for c in baseline]

    def test_it_holds_over_random_punctuation_and_silences(self):
        rng = random.Random(20260905)  # noqa: S311 - a fixed-seed corpus, not a secret
        for _ in range(60):
            texts = [
                w + rng.choice(["", "", "", ",", ".", "?"])
                for w in rng.choices(PHRASE, k=rng.randint(1, 25))
            ]
            source = []
            t = 0.0
            for text in texts:
                gap = rng.choice([0.0, 0.0, 0.05, 0.3, 2.0])
                t += gap
                source.append(Word(text, t, t + 0.35, 0.9))
                t += 0.35
            assert shapes(build_cards(source, breaks=CardBreaks())) == shapes(build_cards(source))

    def test_an_empty_word_list_is_still_no_cards(self):
        assert build_cards([], breaks=CardBreaks(before=frozenset({0}))) == []


class TestForcedBreaks:
    def test_a_marked_word_starts_a_card_the_rules_would_not_have_broken_at(self):
        source = words(["take", "the", "tool", "and", "put", "it", "down", "now", "please"])
        assert shapes(build_cards(source)) == [
            ["take", "the", "tool", "and"],
            ["put", "it", "down", "now", "please"],
        ]
        forced = build_cards(source, breaks=CardBreaks(before=frozenset({1})))
        assert shapes(forced) == [["take"], ["the", "tool", "and", "put"], ["it", "down", "now", "please"]]

    def test_a_marked_word_joins_the_line_the_max_words_rule_would_have_split(self):
        source = words(["take", "the", "tool", "and", "put", "it"])
        assert shapes(build_cards(source, max_words=3)) == [["take", "the", "tool"], ["and", "put", "it"]]
        joined = build_cards(source, max_words=3, breaks=CardBreaks(not_before=frozenset({3})))
        assert shapes(joined) == [["take", "the", "tool", "and", "put", "it"]]

    def test_a_marked_word_joins_across_a_punctuation_break(self):
        source = words(["take", "the", "tool,", "and", "put", "it"])
        assert shapes(build_cards(source)) == [["take", "the", "tool,"], ["and", "put", "it"]]
        joined = build_cards(source, breaks=CardBreaks(not_before=frozenset({3})))
        assert shapes(joined) == [["take", "the", "tool,", "and", "put", "it"]]

    def test_a_marked_word_joins_across_a_silence(self):
        source = [
            Word("take", 0.0, 0.4, 0.9),
            Word("the", 0.4, 0.8, 0.9),
            Word("tool", 0.8, 1.2, 0.9),
            Word("now", 5.0, 5.4, 0.9),
            Word("please", 5.4, 5.8, 0.9),
            Word("stop", 5.8, 6.2, 0.9),
        ]
        assert shapes(build_cards(source)) == [["take", "the", "tool"], ["now", "please", "stop"]]
        joined = build_cards(source, breaks=CardBreaks(not_before=frozenset({3})))
        assert shapes(joined) == [["take", "the", "tool", "now", "please", "stop"]]

    def test_a_forced_break_on_the_tail_is_not_folded_back_into_the_line_above(self):
        source = words(["take", "the", "tool", "and", "put"])
        assert shapes(build_cards(source, max_words=4)) == [["take", "the", "tool", "and", "put"]]
        forced = build_cards(source, max_words=4, breaks=CardBreaks(before=frozenset({4})))
        assert shapes(forced) == [["take", "the", "tool", "and"], ["put"]]

    def test_a_break_on_the_first_word_is_a_no_op(self):
        source = words()
        assert shapes(build_cards(source, breaks=CardBreaks(before=frozenset({0})))) == shapes(build_cards(source))

    def test_a_marker_never_loses_or_reorders_a_word(self):
        rng = random.Random(4)  # noqa: S311 - a fixed-seed corpus, not a secret
        source = words()
        for _ in range(40):
            indexes = frozenset(rng.sample(range(len(source)), k=rng.randint(1, 5)))
            other = frozenset(rng.sample(range(len(source)), k=rng.randint(1, 5))) - indexes
            cards = build_cards(source, breaks=CardBreaks(before=indexes, not_before=other))
            flat = [w.text for card in cards for w in card.words]
            assert flat == [w.text for w in source]

    def test_a_word_told_both_things_is_joined_not_split(self):
        # The record can never hold both (split and merge clear each other),
        # but the engine must still be total if a caller hands it both.
        source = words(["take", "the", "tool", "and", "put", "it"])
        cards = build_cards(source, breaks=CardBreaks(before=frozenset({3}), not_before=frozenset({3})))
        assert shapes(cards) == [["take", "the", "tool", "and", "put", "it"]]
