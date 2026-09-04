"""Unit tests for the glow halo builder (design 2026-09-04, section 3).

The exact tag strings matter: they were rendered through the real libass
and produce a halo behind a readable word. ``test_glow_real.py`` proves the
pixels; this file pins the text.
"""
from __future__ import annotations

from ash_captions.styles.render_glow import (
    HALO_LAYER,
    POP_HALF_MS,
    TEXT_LAYER,
    glow_width,
    halo_line_text,
    scale_transform_tags,
)
from ash_captions.styles.schema import Style


def _style(size: int = 88, scale: float = 1.1) -> Style:
    return Style.from_dict(
        {
            "name": "G",
            "size": size,
            "colors": {"text": "#FFFFFF", "active": "#4DFFC3", "outline": "#00332A"},
            "active_word": {"effect": "glow", "scale": scale},
        },
        check_font=False,
    )


def test_halo_layer_draws_under_the_text_layer():
    assert HALO_LAYER == 0
    assert TEXT_LAYER == 1


def test_scale_transform_tags_are_the_pop_effect_transform():
    assert POP_HALF_MS == 90
    assert scale_transform_tags(1.12) == "\\t(0,90,\\fscx112\\fscy112)\\t(90,180,\\fscx100\\fscy100)"
    assert scale_transform_tags(1.0, half_ms=40) == "\\t(0,40,\\fscx100\\fscy100)\\t(40,80,\\fscx100\\fscy100)"


def test_glow_width_is_double_the_outline_and_at_least_3px_wider():
    assert glow_width(_style(size=80)) == 8  # base outline 4 -> max(4 + 3, 4 * 2)
    assert glow_width(_style(size=30)) == 5  # base outline 2 -> max(2 + 3, 2 * 2)


def test_halo_line_hides_every_other_word_and_hollows_the_active_one():
    text = halo_line_text(["one", "GLOW", "two"], 1, _style())
    assert text == (
        "{\\alpha&HFF&}one "
        "{\\1a&HFF&\\3a&H00&\\4a&HFF&\\3c&HC3FF4D&\\bord10\\blur4\\be1"
        "\\t(0,90,\\fscx110\\fscy110)\\t(90,180,\\fscx100\\fscy100)}GLOW"
        "{\\alpha&HFF&\\bord5\\blur0\\be0\\fscx100\\fscy100} "
        "{\\alpha&HFF&}two"
    )


def test_halo_line_for_a_one_word_card_has_no_hidden_words():
    text = halo_line_text(["GLOW"], 0, _style())
    assert text.startswith("{\\1a&HFF&")
    assert text.count("{\\alpha&HFF&}") == 0
    assert text.endswith("GLOW{\\alpha&HFF&\\bord5\\blur0\\be0\\fscx100\\fscy100}")


def test_halo_never_shows_a_fill_or_a_shadow():
    # \1a = fill alpha, \4a = shadow alpha; both must be fully transparent on
    # the halo layer or the halo doubles the text's own fill/shadow.
    text = halo_line_text(["a", "b"], 0, _style())
    assert "\\1a&HFF&" in text
    assert "\\4a&HFF&" in text
    assert "\\c&" not in text  # no fill colour tags at all on this layer
