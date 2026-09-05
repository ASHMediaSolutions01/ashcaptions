"""Free placement: several treatments inside one caption, words that
stay (design 2026-09-05, section 5).

A look in ``layout.mode = "free"`` carries a list of ``Slot``s instead of
laying its words out as a line. This module turns a card into **one
Dialogue event per word**, each pinned to its slot with ``\\pos``, each
with its own scale, colour, face, lean and entrance -- and each **ending
when the card ends** rather than when the next word starts. That last
detail is the whole effect: the words accumulate on screen instead of
replacing one another, which is what the owner's reference reels do.

The other half is ``assign_slots``, a pure function over a card. Nobody
is going to hand-place seven hundred words, so the look lays them out and
the editor drags what landed badly: a short connector ("the", "on", "a",
"and", "in") takes the smallest slot, the long content word takes the
biggest, and the same phrase always lays out the same way.

Two entrances, both measured off the owner's own frames:

  * ``stretch_collapse`` -- the word enters ~180% wide and snaps back to
    100% over ~120 ms while fading in.
  * ``fade_settle`` -- fades in over ~240 ms while shrinking from ~110%
    and dropping a few pixels into place.

``\\pos`` and ``\\move`` do not compose in libass, and neither do two
``\\fad`` tags on one line -- the same traps ``render.py`` documents -- so
a settling word is placed by its ``\\move`` and entrance/exit fades are
merged into a single ``\\fad(in, out)``.

Import direction: ``render.py`` imports this module; the two helpers
needed from it (text preparation, number formatting, the Dialogue line)
are imported inside ``free_events`` rather than at module scope, so there
is no cycle and ``render.py`` stays the owner of that shared code.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from ..engine.rules import Card
from ..engine.transcribe import Word
from .ass_format import ass_inline_colour, outline_width
from .schema import Slot, Style

# Entrance geometry, from the reference frames (see the module docstring).
STRETCH_ENTER_SCALE = 1.8
STRETCH_MS = 120
SETTLE_ENTER_SCALE = 1.1
SETTLE_MS = 240
SETTLE_DROP_PX = 10

_ENTRANCE_MS = {"stretch_collapse": STRETCH_MS, "fade_settle": SETTLE_MS}


class Placement(Protocol):
    """The two fields ``free_events`` reads off a per-word style.

    Track B owns ``WordStyle`` in ``schema.py`` (design 2026-09-05,
    "Interfaces"); this module only ever needs ``x`` and ``y``, so it
    describes the shape structurally rather than importing a dataclass
    that would then exist twice.
    """

    x: float | None
    y: float | None


# ---------------------------------------------------------------------------
# slot assignment -- a pure function over a card
# ---------------------------------------------------------------------------

# The closed word classes: articles, prepositions, conjunctions,
# pronouns and the common auxiliaries. A word here is a *connector* and
# sinks to the least prominent slot; everything else is *content* and
# rises. Deliberately a fixed list rather than a length threshold -- the
# reference frame's biggest word is "2nd", three characters long.
CONNECTOR_WORDS = frozenset(
    {
        # articles and determiners
        "a", "an", "the", "this", "that", "these", "those", "there", "here",
        "all", "any", "some", "each", "every", "no", "such", "its",
        # conjunctions and subordinators
        "and", "but", "or", "nor", "so", "yet", "as", "if", "than", "though",
        "when", "while", "because", "since", "until", "unless", "whether",
        # prepositions
        "at", "by", "for", "from", "in", "into", "of", "off", "on", "onto",
        "out", "over", "to", "up", "with", "within", "without", "near",
        "under", "upon", "about", "after", "before", "between", "through",
        # pronouns
        "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
        "she", "her", "it", "they", "them", "their", "who", "whom", "whose",
        # auxiliaries and other high-frequency function words
        "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
        "did", "has", "have", "had", "will", "would", "can", "could", "shall",
        "should", "may", "might", "must", "not", "just", "very", "too",
    }
)

_EDGE_PUNCTUATION = re.compile(r"^[^\w']+|[^\w']+$", re.UNICODE)


def normalise_word(text: str) -> str:
    """A word's comparison form: lower case, edge punctuation removed,
    an internal apostrophe kept ("Westbrook's," -> "westbrook's")."""
    return _EDGE_PUNCTUATION.sub("", text.strip().lower())


FIGURE, CONTENT, CONNECTOR = 0, 1, 2


def prominence_rank(text: str) -> int:
    """How much of the frame a word deserves: ``FIGURE`` (0) for anything
    carrying a digit, ``CONNECTOR`` (2) for a closed-class function word,
    ``CONTENT`` (1) for everything else.

    Figures outrank ordinary content on purpose. "$4.2m", "2nd", "24/7"
    are the words these looks exist to enlarge, and burning "worth
    $4.2m in Malibu" through the big-number look with figures merely
    counted as content put "worth" on the 2.85x slot -- which is not the
    look anybody asked for."""
    normalised = normalise_word(text)
    if any(character.isdigit() for character in normalised):
        return FIGURE
    if normalised and normalised in CONNECTOR_WORDS:
        return CONNECTOR
    return CONTENT


def is_connector(text: str) -> bool:
    """True for the short function words that take the small italic
    treatment. A token carrying a digit is never one, whatever it looks
    like: "2nd" is the word the big-number look exists for."""
    return prominence_rank(text) == CONNECTOR


def assign_slots(words: Sequence[Word], slots: Sequence[Slot]) -> tuple[int, ...]:
    """Which slot each word of a card takes -- the pure function the
    whole look rests on.

    The first ``len(words)`` slots are the ones in play (declaration
    order is the look author's reading order). They are ranked by
    ``scale``, largest first, ties broken by declaration index. The words
    are ranked by ``prominence_rank`` -- figures, then content, then
    connectors -- each group in the order it is spoken, and the two
    rankings are zipped together. So a card reading "the 2nd Highest
    residential" puts "2nd" on the biggest slot, "Highest" on the next,
    "residential" on the next, and "the" on the small italic one -- which
    is the reference frame.

    Deterministic: the same phrase always lays out the same way. A card
    with more words than the look has slots cycles through them; a
    validated look can never be in that position (``schema`` requires
    ``len(slots) >= max_words``), but the function stays total.
    """
    count = len(words)
    if count == 0 or not slots:
        return ()
    if count > len(slots):
        return tuple(index % len(slots) for index in range(count))

    by_prominence = sorted(range(count), key=lambda index: (-slots[index].scale, index))
    ranked = sorted(range(count), key=lambda index: (prominence_rank(words[index].text), index))

    assignment = [0] * count
    for rank, slot_index in enumerate(by_prominence):
        assignment[ranked[rank]] = slot_index
    return tuple(assignment)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


def free_events(
    card: Card,
    style: Style,
    style_name: str,
    width: int,
    height: int,
    *,
    offset: tuple[float, float] = (0.0, 0.0),
    word_styles: Mapping[tuple[float, float], Placement] | None = None,
) -> list[str]:
    """One Dialogue event per word of ``card``, each at its own slot.

    ``offset`` shifts every slot by the same amount -- it is how the
    Studio's existing drag handle moves a free-placement caption as one
    cluster, and is ``(0, 0)`` for an undragged job. ``word_styles`` is
    the mapping the spec fixes, keyed by a word's ``(start, end)``; only
    ``x``/``y`` are read here, and a word that sets them is placed
    absolutely, without ``offset``, because the editor chose that point
    on the frame itself.

    ``render.py``'s branch does not pass ``word_styles`` yet: track B is
    adding it to ``render_ass`` and ``_card_events``, and forwarding it
    here is a one-argument change once that lands.
    """
    from .render import _dialogue_line, _num, _prepare_word_text

    slots = style.layout.slots
    if not slots:
        return []
    words = card.words
    assignment = assign_slots(words, slots)
    exit_ms = style.exit.duration_ms if style.exit.effect == "fade" else 0

    lines: list[str] = []
    for index, word in enumerate(words):
        slot = slots[assignment[index]]
        start = word.start
        end = card.end if card.end > start else start + 0.01
        event_ms = max(1, round((end - start) * 1000))
        x, y = _slot_point(slot, width, height, offset, _placement_for(word_styles, word))
        tags = _word_tags(style, slot, x, y, event_ms=event_ms, exit_ms=exit_ms, num=_num)
        text = _prepare_word_text(word.text, style)
        lines.append(_dialogue_line(start, end, style_name, f"{{{tags}}}{text}"))
    return lines


def _placement_for(
    word_styles: Mapping[tuple[float, float], Placement] | None, word: Word
) -> Placement | None:
    if not word_styles:
        return None
    return word_styles.get((word.start, word.end))


def _slot_point(
    slot: Slot,
    width: int,
    height: int,
    offset: tuple[float, float],
    placement: Placement | None,
) -> tuple[float, float]:
    override_x = getattr(placement, "x", None) if placement is not None else None
    override_y = getattr(placement, "y", None) if placement is not None else None
    x = slot.x * width + offset[0] if override_x is None else float(override_x) * width
    y = slot.y * height + offset[1] if override_y is None else float(override_y) * height
    return x, y


def _word_tags(
    style: Style, slot: Slot, x: float, y: float, *, event_ms: int, exit_ms: int, num
) -> str:
    scale = round(slot.scale * 100)
    enter_ms = min(_ENTRANCE_MS.get(slot.entrance, 0), event_ms)
    tags = ["\\an5"]  # \pos is the word's centre, whatever the look's align is

    if slot.entrance == "fade_settle" and enter_ms:
        # \move places the word as well as animating it; \pos alongside
        # it would silently win in libass, so it is never emitted here.
        tags.append(f"\\move({num(x)},{num(y - SETTLE_DROP_PX)},{num(x)},{num(y)},0,{enter_ms})")
    else:
        tags.append(f"\\pos({num(x)},{num(y)})")

    tags.append(f"\\fn{slot.font or style.font}")
    if slot.italic:
        tags.append("\\i1")
    # libass does not scale the border with \fscx/\fscy -- it is a Style
    # line property in outline units, and a burned frame confirmed it: at
    # size 200 every slot came out with the same 11px border whatever its
    # scale, so a 0.5x word wore an outline twice as heavy, relatively, as
    # a normal caption's. Scale it here, so small words look small.
    tags.append(f"\\bord{_border(style, slot)}")
    if style.letter_spacing:
        tags.append(f"\\fsp{num(style.letter_spacing)}")
    tags.append(f"\\c{ass_inline_colour(getattr(style.colors, slot.role))}")
    tags.extend(_scale_tags(slot.entrance, scale, enter_ms))

    fade = _fade_tag(enter_ms if slot.entrance != "none" else 0, exit_ms, event_ms)
    if fade:
        tags.append(fade)
    return "".join(tags)


def _border(style: Style, slot: Slot) -> int:
    """The slot's outline width in pixels. ``slot.border`` of 0 means no
    outline at all -- what a word drawn in the look's *outline* colour
    needs, since a black fill inside a black border is a slab, not a
    word (seen on a burned frame, not guessed)."""
    if not slot.border:
        return 0
    return max(1, round(outline_width(style) * slot.scale * slot.border))


def _scale_tags(entrance: str, scale: int, enter_ms: int) -> list[str]:
    if not enter_ms or entrance == "none":
        return [f"\\fscx{scale}\\fscy{scale}"]
    if entrance == "stretch_collapse":
        # Wide, not tall: the reference stretches horizontally and snaps.
        return [
            f"\\fscx{round(scale * STRETCH_ENTER_SCALE)}\\fscy{scale}",
            f"\\t(0,{enter_ms},\\fscx{scale})",
        ]
    entered = round(scale * SETTLE_ENTER_SCALE)
    return [
        f"\\fscx{entered}\\fscy{entered}",
        f"\\t(0,{enter_ms},\\fscx{scale}\\fscy{scale})",
    ]


def _fade_tag(enter_ms: int, exit_ms: int, event_ms: int) -> str:
    """One merged ``\\fad``: every word of a free card ends together, so
    an event can carry both halves, and two ``\\fad`` tags on one line do
    not compose."""
    if not enter_ms and not exit_ms:
        return ""
    if enter_ms + exit_ms > event_ms:
        enter_ms = min(enter_ms, event_ms // 2)
        exit_ms = event_ms - enter_ms
    return f"\\fad({enter_ms},{exit_ms})"
