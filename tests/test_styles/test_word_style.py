"""``WordStyle`` -- the per-word override of v0.6 design section 2.

Track F builds on these exact field names and bounds, so they are pinned
here rather than left to the renderer's tests to imply.
"""
from __future__ import annotations

import pytest

from ash_captions.styles.schema import (
    TRANSITION_EFFECTS,
    WORD_ANIMATIONS,
    StyleValidationError,
    WordStyle,
)


def test_every_field_is_optional_and_defaults_to_none():
    ws = WordStyle()
    assert (
        ws.colour, ws.scale, ws.bold, ws.italic, ws.x, ws.y,
        ws.animation, ws.duration_ms,
    ) == (None,) * 8
    assert ws.is_empty()
    assert ws.to_dict() == {}


def test_round_trips_through_dict():
    data = {"colour": "#FFD166", "scale": 1.25, "bold": True, "italic": False, "x": 0.5, "y": 0.25}
    ws = WordStyle.from_dict(data)
    assert ws == WordStyle(colour="#FFD166", scale=1.25, bold=True, italic=False, x=0.5, y=0.25)
    assert ws.to_dict() == data
    assert not ws.is_empty()


def test_empty_dict_is_an_empty_override():
    assert WordStyle.from_dict({}).is_empty()


def test_to_dict_leaves_out_what_was_never_set():
    # One override rides on a word in the transcript record; absent keys
    # must not be written back as nulls.
    assert WordStyle(colour="#FFFFFF").to_dict() == {"colour": "#FFFFFF"}
    assert WordStyle(bold=False).to_dict() == {"bold": False}


def test_an_eight_digit_hex_colour_is_accepted():
    assert WordStyle.from_dict({"colour": "#FFD16680"}).colour == "#FFD16680"


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"colour": "FFD166"}, "style.colour"),
        ({"colour": "#GGGGGG"}, "style.colour"),
        ({"colour": 16}, "style.colour"),
        ({"scale": 0.49}, "style.scale"),
        ({"scale": 3.01}, "style.scale"),
        ({"scale": "big"}, "style.scale"),
        ({"scale": True}, "style.scale"),
        ({"bold": "yes"}, "style.bold"),
        ({"italic": 1}, "style.italic"),
        ({"x": -0.01}, "style.x"),
        ({"y": 1.01}, "style.y"),
        ({"font": "Inter"}, "unknown field"),
        ({"outline": "#000000"}, "unknown field"),
    ],
)
def test_bad_values_are_rejected_by_name(data, fragment):
    with pytest.raises(StyleValidationError) as excinfo:
        WordStyle.from_dict(data)
    assert fragment in str(excinfo.value)


def test_the_bounds_themselves_are_inclusive():
    assert WordStyle.from_dict({"scale": 0.5}).scale == 0.5
    assert WordStyle.from_dict({"scale": 3.0}).scale == 3.0
    assert WordStyle.from_dict({"x": 0.0, "y": 1.0}) == WordStyle(x=0.0, y=1.0)


def test_font_and_outline_are_not_fields():
    # The design's line: per-word bold, italic, colour and size; font and
    # outline stay properties of the look.
    assert not hasattr(WordStyle(), "font")
    assert not hasattr(WordStyle(), "outline")


def test_the_path_in_the_error_can_name_where_the_override_came_from():
    with pytest.raises(StyleValidationError) as excinfo:
        WordStyle.from_dict({"scale": 9}, path="words[12].style")
    assert "words[12].style.scale" in str(excinfo.value)


# ---------------------------------------------------------------------------
# per-word animation (v0.7 design item 3)
# ---------------------------------------------------------------------------


def test_a_word_can_carry_its_own_animation_and_duration():
    ws = WordStyle.from_dict({"animation": "bounce", "duration_ms": 240})
    assert (ws.animation, ws.duration_ms) == ("bounce", 240)
    assert ws.to_dict() == {"animation": "bounce", "duration_ms": 240}
    assert not ws.is_empty()


def test_the_offered_animations_are_the_ones_a_single_word_can_actually_do():
    r"""rise and slide are ``\move``, which places a whole line: offering
    them per word would be a control that silently moves something else."""
    assert WORD_ANIMATIONS == {"none", "fade", "zoom", "blur", "blink", "bounce"}
    assert WORD_ANIMATIONS < TRANSITION_EFFECTS
    for name in sorted(WORD_ANIMATIONS):
        assert WordStyle.from_dict({"animation": name}).animation == name


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"animation": "slide"}, "style.animation"),
        ({"animation": "rise"}, "style.animation"),
        ({"animation": "wobble"}, "style.animation"),
        ({"duration_ms": -1}, "style.duration_ms"),
        ({"duration_ms": 2001}, "style.duration_ms"),
    ],
)
def test_a_bad_animation_names_the_field_it_came_from(data, fragment):
    with pytest.raises(StyleValidationError) as excinfo:
        WordStyle.from_dict(data)
    assert fragment in str(excinfo.value)
