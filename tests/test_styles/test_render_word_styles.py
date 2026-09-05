"""``render_ass(..., word_styles=...)`` -- one word styled differently from
its neighbours (v0.6 design, section 2).

The first thing proved here is the thing that must never break: with no
overrides, or an empty mapping, the renderer emits exactly the ``.ass`` it
emitted before the feature existed. ``test_render_golden.py`` pins the
bytes; this file pins that the new keyword changes none of them.
"""
from __future__ import annotations

import pytest

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.render import render_ass
from ash_captions.styles.schema import Style, WordStyle

from .test_render_golden import FIXTURE_STYLES, golden_cards

AMBER_INLINE = "\\c&H66D1FF&"  # the override, #FFD166, as ASS's &HBBGGRR&
# The look's own colours, chosen so neither can be mistaken for the override.
LOOK_COLOURS = {"text": "#FFFFFF", "active": "#00E28A"}
TEXT_INLINE = "\\c&HFFFFFF&"
ACTIVE_INLINE = "\\c&H8AE200&"

WORDS = (
    Word(text="hello", start=0.0, end=0.3),
    Word(text="there", start=0.3, end=0.6),
    Word(text="world", start=0.6, end=1.0),
)
CARD = Card(words=WORDS, start=0.0, end=1.0)
THERE = (0.3, 0.6)


def look(effect: str = "pop", **over) -> Style:
    definition = {
        "name": "T",
        "colors": dict(LOOK_COLOURS),
        "active_word": {"effect": effect, "scale": 1.12},
    }
    definition.update(over)
    return Style.from_dict(definition, check_font=False)


def events(style: Style, word_styles=None) -> list[str]:
    out = render_ass([CARD], style, word_styles=word_styles)
    return [line for line in out.splitlines() if line.startswith("Dialogue:")]


def event_where(style: Style, word_styles, active: int) -> str:
    """The Dialogue event during which ``words[active]`` is the active word."""
    return events(style, word_styles)[active]


# ---------------------------------------------------------------------------
# nothing changes without an override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effect", sorted(FIXTURE_STYLES))
def test_no_overrides_renders_byte_identical_ass(effect):
    style = Style.from_dict(FIXTURE_STYLES[effect], check_font=False)
    cards = golden_cards()
    baseline = render_ass(cards, style, play_res=(1080, 1920))
    assert render_ass(cards, style, play_res=(1080, 1920), word_styles={}) == baseline
    assert render_ass(cards, style, play_res=(1080, 1920), word_styles=None) == baseline


@pytest.mark.parametrize("effect", sorted(FIXTURE_STYLES))
def test_an_override_on_no_word_of_this_job_changes_nothing(effect):
    style = Style.from_dict(FIXTURE_STYLES[effect], check_font=False)
    cards = golden_cards()
    stray = {(99.0, 99.5): WordStyle(colour="#FFD166", scale=2.0, bold=True)}
    assert render_ass(cards, style, play_res=(1080, 1920), word_styles=stray) == render_ass(
        cards, style, play_res=(1080, 1920)
    )


def test_a_glow_look_is_unchanged_too():
    style = look("glow")
    assert events(style, {}) == events(style)


def test_an_override_that_sets_nothing_renders_as_the_look():
    style = look()
    assert events(style, {THERE: WordStyle()}) == events(style)


# ---------------------------------------------------------------------------
# one property at a time
# ---------------------------------------------------------------------------


def test_colour_lands_on_the_word_and_the_line_goes_back_to_the_look():
    line = event_where(look(), {THERE: WordStyle(colour="#FFD166")}, active=0)
    assert f"{AMBER_INLINE}}}there{{" in line
    # the active word before it, and the plain word after it, keep the look's
    assert ACTIVE_INLINE in line.split("}hello")[0]
    assert line.endswith(f"{{{TEXT_INLINE}}}world")


def test_a_colour_wins_over_the_look_because_it_is_emitted_last():
    line = event_where(look(), {THERE: WordStyle(colour="#FFD166")}, active=1)
    block = line.split("}there")[0].split("{")[-1]
    assert block.rindex("\\c&H66D1FF&") > block.index("\\c")  # the look's \c came first


def test_size_scales_a_word_that_is_not_the_active_one():
    line = event_where(look(), {THERE: WordStyle(scale=1.25)}, active=0)
    assert "\\fscx125\\fscy125}there{\\fscx100\\fscy100}" in line


def test_bold_and_italic_open_and_close_around_the_word():
    line = event_where(look(), {THERE: WordStyle(bold=True, italic=True)}, active=0)
    assert "\\b1\\i1}there{\\b0\\i0}" in line


def test_bold_false_is_an_override_too_not_an_absence():
    line = event_where(look(), {THERE: WordStyle(bold=False)}, active=0)
    assert "\\b0}there{\\b0}" in line


def test_two_words_can_carry_different_overrides_in_one_line():
    styles = {
        (0.0, 0.3): WordStyle(colour="#FFD166", scale=1.4),
        THERE: WordStyle(italic=True),
    }
    line = event_where(look(), styles, active=2)
    assert "\\fscx140\\fscy140}hello{" in line
    assert AMBER_INLINE in line.split("}hello")[0]
    assert "\\i1}there{" in line


# ---------------------------------------------------------------------------
# size composes with the active-word pop rather than replacing it
# ---------------------------------------------------------------------------


def test_a_scaled_word_pops_from_its_own_size():
    line = event_where(look("pop"), {THERE: WordStyle(scale=1.25)}, active=1)
    # 1.12 pop on a 125% word: 125 -> 140 -> 125, never 100.
    assert "\\fscx125\\fscy125\\t(0,90,\\fscx140\\fscy140)\\t(90,180,\\fscx125\\fscy125)" in line


def test_an_unscaled_word_pops_exactly_as_before():
    line = event_where(look("pop"), {THERE: WordStyle(colour="#FFD166")}, active=1)
    assert "\\t(0,90,\\fscx112\\fscy112)\\t(90,180,\\fscx100\\fscy100)" in line


def test_a_shake_look_carries_the_size_as_a_plain_tag():
    # No \t chain to fold the size into, so it is appended like the rest.
    line = event_where(look("shake"), {THERE: WordStyle(scale=0.75)}, active=1)
    assert "\\frz0)\\fscx75\\fscy75}there{" in line


# ---------------------------------------------------------------------------
# every look family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effect", ["pop", "none", "shake", "card_box", "glow", "box", "scale_box", "karaoke"])
def test_a_per_word_colour_reaches_every_look_family(effect):
    style = look(effect)
    rendered = "\n".join(events(style, {THERE: WordStyle(colour="#FFD166")}))
    on_there = [
        block for block in rendered.split("there")[:-1] if AMBER_INLINE in block.rsplit("{", 1)[-1]
    ]
    assert on_there, rendered


def test_the_boxed_word_carries_its_own_colour_and_size():
    line = events(look("box"), {THERE: WordStyle(colour="#FFD166", scale=1.25)})[1]
    assert line.endswith(f"{AMBER_INLINE}\\fscx125\\fscy125}}there")


def test_scale_box_bounces_from_the_word_s_own_size():
    line = events(look("scale_box"), {THERE: WordStyle(scale=1.25)})[1]
    assert "{\\fscx125\\fscy125\\t(0,90,\\fscx140\\fscy140)\\t(90,180,\\fscx125\\fscy125)}there" in line


def test_karaoke_colours_the_word_in_both_halves_of_the_sweep():
    line = events(look("karaoke"), {THERE: WordStyle(colour="#FFD166")})[0]
    assert f"\\kf30{AMBER_INLINE}\\2c&H66D1FF&}}there{{" in line
    # ...and the look's own colours are back for the word after it
    assert f"{ACTIVE_INLINE}\\2c&HFFFFFF&}}" in line


def test_karaoke_leaves_an_unstyled_word_exactly_as_before():
    line = events(look("karaoke"), {THERE: WordStyle(colour="#FFD166")})[0]
    assert "{\\kf30}hello" in line and "{\\kf40}world" in line


# ---------------------------------------------------------------------------
# glow: the halo has to keep the text layer's metrics
# ---------------------------------------------------------------------------


def glow_layers(word_styles, active: int) -> tuple[str, str]:
    lines = events(look("glow"), word_styles)
    return lines[active * 2], lines[active * 2 + 1]


def test_the_halo_takes_the_word_s_own_colour():
    halo, _text = glow_layers({THERE: WordStyle(colour="#FFD166")}, active=1)
    assert "\\3c&H66D1FF&" in halo
    assert halo.startswith("Dialogue: 0,")


def test_the_halo_pops_from_the_word_s_own_size_like_the_text_layer():
    halo, text = glow_layers({THERE: WordStyle(scale=1.25)}, active=1)
    chain = "\\fscx125\\fscy125\\t(0,90,\\fscx140\\fscy140)\\t(90,180,\\fscx125\\fscy125)"
    assert chain in halo and chain in text


def test_a_hidden_halo_word_keeps_the_metrics_of_the_visible_one():
    # Otherwise every halo after a resized word slides off its word.
    halo, text = glow_layers({THERE: WordStyle(scale=1.25, bold=True)}, active=0)
    assert "\\b1\\fscx125\\fscy125}there{\\b0\\fscx100\\fscy100}" in halo
    assert "\\b1\\fscx125\\fscy125}there{" in text


def test_a_halo_word_with_no_override_is_untouched():
    halo, _text = glow_layers({THERE: WordStyle(scale=1.25)}, active=0)
    assert "\\3c&H8AE200&" in halo  # the active word's halo is still the look's colour
    assert halo.endswith("{\\alpha&HFF&}world")


# ---------------------------------------------------------------------------
# free placement is track F's, not this renderer's
# ---------------------------------------------------------------------------


def test_x_and_y_are_carried_by_the_schema_but_ignored_by_the_line_renderer():
    style = look()
    assert events(style, {THERE: WordStyle(x=0.2, y=0.8)}) == events(style)
