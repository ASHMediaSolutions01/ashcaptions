"""Editorial layer: turns transcribed words into caption cards.

Pure logic, no I/O -- deterministic and fully unit-testable. This module
is what makes caption timing "feel professional" (spec 9.3):

  - group words into 3-4 word cards
  - enforce a minimum 0.5s on-screen duration (no flicker)
  - snap small gaps between adjacent cards so captions don't blink off
    and back on for a few frames between what is really one thought
  - prefer breaking cards at punctuation rather than mid-clause
  - drop cards that are isolated inside long silences

Callers pass in ``Word`` objects from ``ash_captions.engine.transcribe``.

Note on "drop cards that fall inside silence" (spec 9.3): the design doc
does not define what counts as silence at this layer, since VAD filtering
already happens upstream in transcribe.py. This module treats a single
isolated word -- one with a large time gap on *both* sides -- as a stray
blip that survived VAD, and drops it. ``SILENCE_GAP_SECONDS`` is a
reasonable default pending review against real client audio, not a value
from the spec.
"""
from __future__ import annotations

from dataclasses import dataclass

from .transcribe import Word

MIN_WORDS_PER_CARD = 3
MAX_WORDS_PER_CARD = 4
MIN_CARD_DURATION_SECONDS = 0.5
GAP_SNAP_THRESHOLD_SECONDS = 0.20
SILENCE_GAP_SECONDS = 1.5

SENTENCE_END = (".", "!", "?")
CLAUSE_BREAK = (",", ";", ":")


@dataclass(frozen=True, slots=True)
class Card:
    """One on-screen caption: a handful of words sharing one time range."""

    words: tuple[Word, ...]
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass(frozen=True, slots=True)
class CardBreaks:
    """Where the editor has overruled the line breaks this module would
    pick (v0.6 section 1): word indexes into the list handed to
    ``build_cards`` that must start a new card, and ones that must not.

    Both empty -- and ``breaks=None`` -- is the behaviour every version
    before v0.6 had, byte for byte.
    """

    before: frozenset[int] = frozenset()
    not_before: frozenset[int] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not self.before and not self.not_before


_NO_BREAKS = CardBreaks()


def build_cards(
    words: list[Word] | tuple[Word, ...],
    *,
    min_words: int = MIN_WORDS_PER_CARD,
    max_words: int = MAX_WORDS_PER_CARD,
    min_duration: float = MIN_CARD_DURATION_SECONDS,
    gap_snap_threshold: float = GAP_SNAP_THRESHOLD_SECONDS,
    silence_gap: float = SILENCE_GAP_SECONDS,
    breaks: CardBreaks | None = None,
) -> list[Card]:
    """Turn a flat word list into professionally-timed caption cards.

    Applies, in order: punctuation-aware grouping, silence-isolated card
    dropping, gap-snapping between what remains, then the minimum-duration
    floor. Each step is also exposed standalone below for focused testing.

    ``breaks`` carries the editor's own line breaks (``CardBreaks``); the
    default ``None`` is exactly the behaviour before it existed.
    """
    if not words:
        return []

    groups = _group_words(
        list(words),
        min_words=min_words,
        max_words=max_words,
        silence_gap=silence_gap,
        breaks=breaks or _NO_BREAKS,
    )
    cards = [Card(words=tuple(group), start=group[0].start, end=group[-1].end) for group in groups]
    cards = _drop_silent_cards(cards, silence_gap=silence_gap)
    if not cards:
        return []
    cards = _snap_gaps(cards, gap_snap_threshold=gap_snap_threshold)
    cards = _enforce_min_duration(cards, min_duration=min_duration)
    return cards


def _group_words(
    words: list[Word], *, min_words: int, max_words: int, silence_gap: float, breaks: CardBreaks = _NO_BREAKS
) -> list[list[Word]]:
    """Greedily group words into cards of ``min_words``-``max_words`` words.

    Breaks early (before ``max_words``) once ``min_words`` is reached if the
    current word ends a sentence or clause, so cards don't split mid-clause
    where a natural pause already exists. A gap of ``silence_gap`` or more
    between two consecutive words always forces a break too, regardless of
    word count -- a card must never span a real silence. A trailing group
    shorter than ``min_words`` is folded into the previous card rather than
    left as a stray one- or two-word caption, unless it is the only group or
    a silence gap separates it from that previous card.

    ``breaks`` overrules all of that for the words it names: one in
    ``before`` always starts a card, one in ``not_before`` never does. The
    break a word triggers lands *before the next word*, so it is carried in
    ``pending`` rather than closing the group on the spot -- that is what
    lets ``not_before`` veto it.
    """
    groups: list[list[Word]] = []
    current: list[Word] = []
    pending = False  # the word just added asked for a break after it
    for index, word in enumerate(words):
        if current and index not in breaks.not_before:
            gap = (word.start - current[-1].end) >= silence_gap
            if pending or gap or index in breaks.before:
                groups.append(current)
                current = []
        pending = False

        current.append(word)
        at_max = len(current) >= max_words
        stripped = word.text.rstrip()
        at_sentence_end = stripped.endswith(SENTENCE_END)
        at_clause_break = stripped.endswith(CLAUSE_BREAK)
        pending = at_max or (
            len(current) >= min_words and (at_sentence_end or at_clause_break)
        )

    if current:
        # ``pending`` here means the last word closed its own group, which
        # before this parameter existed left nothing to fold.
        first = len(words) - len(current)
        separated_by_silence = groups and (current[0].start - groups[-1][-1].end) >= silence_gap
        forced_apart = pending or first in breaks.before
        if groups and len(current) < min_words and not separated_by_silence and not forced_apart:
            groups[-1] = groups[-1] + current
        else:
            groups.append(current)

    return groups


def _drop_silent_cards(cards: list[Card], *, silence_gap: float) -> list[Card]:
    """Drop single-word cards isolated by a long silence on both sides."""
    if len(cards) <= 1:
        return cards

    kept: list[Card] = []
    for i, card in enumerate(cards):
        gap_before = card.start - cards[i - 1].end if i > 0 else float("inf")
        gap_after = cards[i + 1].start - card.end if i < len(cards) - 1 else float("inf")
        isolated = gap_before >= silence_gap and gap_after >= silence_gap
        if isolated and len(card.words) == 1:
            continue
        kept.append(card)
    return kept


def _snap_gaps(cards: list[Card], *, gap_snap_threshold: float) -> list[Card]:
    """Close small gaps between adjacent cards at their midpoint.

    A gap larger than ``gap_snap_threshold`` is a real pause and is left
    alone; a smaller one reads as flicker rather than a pause, so the
    previous card's end and the next card's start are moved to meet in
    the middle.
    """
    if not cards:
        return cards

    snapped = [cards[0]]
    for card in cards[1:]:
        prev = snapped[-1]
        gap = card.start - prev.end
        if 0 < gap <= gap_snap_threshold:
            midpoint = prev.end + gap / 2
            snapped[-1] = Card(words=prev.words, start=prev.start, end=midpoint)
            card = Card(words=card.words, start=midpoint, end=card.end)
        snapped.append(card)
    return snapped


def _enforce_min_duration(cards: list[Card], *, min_duration: float) -> list[Card]:
    """Extend any card shorter than ``min_duration`` up to the next card's start.

    Never overlaps into the following card. If the next card starts too
    soon to reach ``min_duration``, the card is extended as far as
    possible and stays short -- there is no room to do better without
    overlapping.
    """
    result: list[Card] = []
    for i, card in enumerate(cards):
        duration = card.end - card.start
        if duration >= min_duration:
            result.append(card)
            continue

        new_end = card.start + min_duration
        next_start = cards[i + 1].start if i + 1 < len(cards) else None
        if next_start is not None:
            new_end = min(new_end, next_start)
        new_end = max(new_end, card.end)
        result.append(Card(words=card.words, start=card.start, end=new_end))
    return result
