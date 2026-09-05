"""``WordStyle`` -- the per-word override of v0.6 design section 2.

Track F builds on these exact field names and bounds, so they are pinned
here rather than left to the renderer's tests to imply.
"""
from __future__ import annotations

import pytest

from ash_captions.styles.schema import StyleValidationError, WordStyle


def test_every_field_is_optional_and_defaults_to_none():
    ws = WordStyle()
    assert (ws.colour, ws.scale, ws.bold, ws.italic, ws.x, ws.y) == (None,) * 6
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
