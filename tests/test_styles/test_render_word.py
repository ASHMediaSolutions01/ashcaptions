"""``styles.render_word`` -- the per-word override primitives, as pure
functions (v0.6 design, section 2).

The rule these all serve: no override, no bytes. Every helper returns
empty strings for ``None``, which is what keeps ``render_ass`` byte-
identical for a transcript that has no overrides.
"""
from __future__ import annotations

from ash_captions.engine.transcribe import Word
from ash_captions.styles.render_word import (
    POP_HALF_MS,
    face_tags,
    karaoke_override_tags,
    override_tags,
    scale_pct,
    scale_transform_tags,
    scaled_transform_tags,
    word_style_for,
)
from ash_captions.styles.schema import Style, WordStyle

WORD = Word(text="herramienta", start=10.82, end=11.66)
AMBER = WordStyle(colour="#FFD166")


def a_style(**over) -> Style:
    return Style.from_dict({"name": "T", **over}, check_font=False)


# ---------------------------------------------------------------------------
# looking a word up
# ---------------------------------------------------------------------------


def test_a_word_is_found_by_its_own_timings():
    assert word_style_for(WORD, {(10.82, 11.66): AMBER}) is AMBER


def test_no_mapping_and_no_match_are_both_none():
    assert word_style_for(WORD, None) is None
    assert word_style_for(WORD, {}) is None
    assert word_style_for(WORD, {(0.0, 1.0): AMBER}) is None


def test_an_override_that_sets_nothing_is_no_override():
    # An empty {} from the API must render as the look, not as a diff.
    assert word_style_for(WORD, {(10.82, 11.66): WordStyle()}) is None


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------


def test_scale_pct_is_100_without_an_override():
    assert scale_pct(None) == 100
    assert scale_pct(WordStyle()) == 100
    assert scale_pct(AMBER) == 100
    assert scale_pct(WordStyle(scale=1.25)) == 125


def test_the_based_transform_matches_the_plain_one_at_100():
    # ...apart from the leading \fscx100\fscy100, which is why render.py
    # keeps using the plain one when the word has no size of its own.
    assert scaled_transform_tags(1.12, 100, POP_HALF_MS) == (
        "\\fscx100\\fscy100" + scale_transform_tags(1.12)
    )


def test_the_pop_runs_from_the_word_s_own_size_and_back_to_it():
    assert scaled_transform_tags(1.12, 125, 90) == (
        "\\fscx125\\fscy125\\t(0,90,\\fscx140\\fscy140)\\t(90,180,\\fscx125\\fscy125)"
    )


# ---------------------------------------------------------------------------
# the tags themselves
# ---------------------------------------------------------------------------


def test_nothing_at_all_for_no_override():
    assert face_tags(None) == ("", "")
    assert override_tags(None) == ("", "")
    assert override_tags(None, restore_colour="&HFFFFFF&") == ("", "")
    assert karaoke_override_tags(None, a_style()) == ("", "")


def test_weight_and_slant_open_and_close():
    assert face_tags(WordStyle(bold=True)) == ("\\b1", "\\b0")
    assert face_tags(WordStyle(bold=False)) == ("\\b0", "\\b0")
    assert face_tags(WordStyle(italic=True)) == ("\\i1", "\\i0")
    assert face_tags(WordStyle(bold=True, italic=True)) == ("\\b1\\i1", "\\b0\\i0")


def test_size_is_a_face_tag_unless_the_caller_baked_it_in():
    ws = WordStyle(scale=1.25)
    assert face_tags(ws) == ("\\fscx125\\fscy125", "\\fscx100\\fscy100")
    assert face_tags(ws, include_scale=False) == ("", "")


def test_colour_is_bgr_and_restores_only_when_asked():
    assert override_tags(AMBER) == ("\\c&H66D1FF&", "")
    assert override_tags(AMBER, restore_colour="&HFFFFFF&") == ("\\c&H66D1FF&", "\\c&HFFFFFF&")


def test_a_full_override_emits_colour_then_weight_then_slant_then_size():
    ws = WordStyle(colour="#FFD166", scale=1.5, bold=True, italic=True)
    assert override_tags(ws, restore_colour="&HFFFFFF&") == (
        "\\c&H66D1FF&\\b1\\i1\\fscx150\\fscy150",
        "\\c&HFFFFFF&\\b0\\i0\\fscx100\\fscy100",
    )


def test_free_placement_is_not_this_renderer_s_business():
    # x/y belong to track F's free-placement look; the line renderer emits
    # nothing for them.
    assert override_tags(WordStyle(x=0.2, y=0.8)) == ("", "")


def test_karaoke_colours_both_halves_of_the_sweep_and_restores_the_look_s():
    style = a_style(colors={"text": "#FFFFFF", "active": "#00E28A"})
    open_tags, close_tags = karaoke_override_tags(AMBER, style)
    assert open_tags == "\\c&H66D1FF&\\2c&H66D1FF&"
    assert close_tags == "\\c&H8AE200&\\2c&HFFFFFF&"


def test_karaoke_carries_weight_slant_and_size_like_any_other_look():
    style = a_style()
    assert karaoke_override_tags(WordStyle(bold=True, scale=0.75), style) == (
        "\\b1\\fscx75\\fscy75",
        "\\b0\\fscx100\\fscy100",
    )
