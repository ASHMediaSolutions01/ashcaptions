"""A per-word override must work in a free-placement look too.

Tracks B and F were built in parallel: B gave every word its own colour,
size, weight and slant; F gave the reel looks one event per word at its own
slot. They only meet here, and the way they fail to meet is silent -- the
editor picks a colour, the Studio shows it, and the reel look ignores it.
"""
from __future__ import annotations

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.render import render_ass
from ash_captions.styles.schema import Style, WordStyle

WORDS = (
    Word(text="the", start=0.0, end=0.3),
    Word(text="2nd", start=0.3, end=0.7),
    Word(text="Highest", start=0.7, end=1.2),
)
CARD = Card(words=WORDS, start=0.0, end=1.6)
SECOND = (0.3, 0.7)
AMBER_INLINE = "\\c&H66D1FF&"  # #FFD166 as ASS's &HBBGGRR&


def reel_look() -> Style:
    return Style.from_dict(
        {
            "name": "T",
            "colors": {"text": "#FFFFFF", "active": "#00E28A"},
            "layout": {
                "mode": "free",
                "max_words": 3,
                "slots": [
                    {"x": 0.30, "y": 0.30, "scale": 0.55, "italic": True},
                    {"x": 0.50, "y": 0.45, "scale": 2.20},
                    {"x": 0.45, "y": 0.62, "scale": 1.15},
                ],
            },
        },
        check_font=False,
    )


def free_events_for(**kwargs) -> list[str]:
    out = render_ass([CARD], reel_look(), play_res=(1080, 1920), **kwargs)
    return [line for line in out.splitlines() if line.startswith("Dialogue:")]


def event_for(word: str, **kwargs) -> str:
    return next(line for line in free_events_for(**kwargs) if line.rstrip().endswith(word))


def test_without_overrides_a_free_look_renders_what_it_always_did():
    plain = render_ass([CARD], reel_look(), play_res=(1080, 1920))
    assert render_ass([CARD], reel_look(), play_res=(1080, 1920), word_styles={}) == plain
    assert render_ass([CARD], reel_look(), play_res=(1080, 1920), word_styles=None) == plain


def test_a_words_own_colour_beats_the_slots_colour():
    line = event_for("2nd", word_styles={SECOND: WordStyle(colour="#FFD166")})
    assert AMBER_INLINE in line
    assert "\\c&HFFFFFF&" not in line  # the slot's own colour is gone from this word


def test_only_that_word_takes_the_colour():
    events = free_events_for(word_styles={SECOND: WordStyle(colour="#FFD166")})
    assert sum(AMBER_INLINE in line for line in events) == 1


def test_a_words_own_size_multiplies_the_slots_size():
    """The slot is a guess from word length and role; the editor's number
    scales it rather than replacing it, so the layout keeps its shape."""
    plain = event_for("2nd")
    scaled = event_for("2nd", word_styles={SECOND: WordStyle(scale=1.5)})
    assert "\\fscx220" in plain and "\\fscy220" in plain
    assert "\\fscx330" in scaled and "\\fscy330" in scaled


def test_bold_and_italic_are_applied_and_can_switch_a_slot_back_off():
    bold = event_for("2nd", word_styles={SECOND: WordStyle(bold=True)})
    assert "\\b1" in bold
    # Slot 0 is italic by declaration; an override of False must win.
    upright = event_for("the", word_styles={(0.0, 0.3): WordStyle(italic=False)})
    assert "\\i1" not in upright


def test_a_words_own_position_overrides_its_slot():
    moved = event_for("2nd", word_styles={SECOND: WordStyle(x=0.1, y=0.9)})
    assert "\\pos(108,1728)" in moved or "\\move(108," in moved
