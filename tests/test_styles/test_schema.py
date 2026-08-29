"""Tests for ash_captions.styles.schema.

Covers spec 7A.4's validation requirements: unknown effect names,
out-of-range values, malformed hex colours, and a font not in the
bundled manifest are all rejected at load time with a message naming
what was wrong.
"""
from __future__ import annotations

import pytest

from ash_captions.styles.schema import (
    ActiveWord,
    Colors,
    Layout,
    Style,
    StyleValidationError,
    Transition,
)


def test_minimal_style_uses_defaults():
    style = Style.from_dict({"name": "MINIMAL"})
    assert style.name == "MINIMAL"
    assert style.font == "Inter"
    assert style.colors == Colors()
    assert style.active_word == ActiveWord()
    assert style.layout == Layout()


def test_full_style_from_spec_example():
    data = {
        "name": "POP BOLD",
        "font": "Montserrat ExtraBold",
        "size": 78,
        "uppercase": True,
        "letter_spacing": 1.5,
        "colors": {"text": "#FFFFFF", "active": "#00E28A", "outline": "#000000"},
        "active_word": {"effect": "scale_box", "scale": 1.18, "box": True},
        "entrance": {"effect": "rise", "duration_ms": 140},
        "layout": {"position": "center", "max_words": 3},
    }
    style = Style.from_dict(data)
    assert style.name == "POP BOLD"
    assert style.size == 78
    assert style.uppercase is True
    assert style.letter_spacing == 1.5
    assert style.colors.active == "#00E28A"
    assert style.active_word.effect == "scale_box"
    assert style.active_word.scale == 1.18
    assert style.entrance.effect == "rise"
    assert style.entrance.duration_ms == 140
    assert style.layout.position == "center"
    assert style.layout.max_words == 3
    # Fields not given still fall back to their defaults.
    assert style.exit == Transition(effect="none", duration_ms=0)


def test_requires_a_name():
    with pytest.raises(StyleValidationError, match="name"):
        Style.from_dict({})


def test_rejects_empty_name():
    with pytest.raises(StyleValidationError, match="name"):
        Style.from_dict({"name": "   "})


def test_rejects_unknown_top_level_field():
    with pytest.raises(StyleValidationError, match="colour"):
        Style.from_dict({"name": "X", "colour": "typo"})


def test_rejects_unknown_active_word_effect():
    with pytest.raises(StyleValidationError, match="active_word.effect"):
        Style.from_dict({"name": "X", "active_word": {"effect": "explode"}})


def test_rejects_unknown_entrance_effect():
    with pytest.raises(StyleValidationError, match="entrance.effect"):
        Style.from_dict({"name": "X", "entrance": {"effect": "teleport"}})


def test_rejects_unknown_layout_position():
    with pytest.raises(StyleValidationError, match="layout.position"):
        Style.from_dict({"name": "X", "layout": {"position": "sideways"}})


@pytest.mark.parametrize(
    "colour",
    ["red", "#GGGGGG", "#FFF", "FFFFFF", "#12345", "#1234567", ""],
)
def test_rejects_malformed_hex_colours(colour):
    with pytest.raises(StyleValidationError, match="colors.text"):
        Style.from_dict({"name": "X", "colors": {"text": colour}})


def test_accepts_8_digit_hex_colour_with_alpha():
    style = Style.from_dict({"name": "X", "colors": {"box": "#00000080"}})
    assert style.colors.box == "#00000080"


def test_rejects_out_of_range_size():
    with pytest.raises(StyleValidationError, match="size"):
        Style.from_dict({"name": "X", "size": 5})
    with pytest.raises(StyleValidationError, match="size"):
        Style.from_dict({"name": "X", "size": 1000})


def test_rejects_out_of_range_scale():
    with pytest.raises(StyleValidationError, match="active_word.scale"):
        Style.from_dict({"name": "X", "active_word": {"scale": 10.0}})


def test_rejects_out_of_range_duration():
    with pytest.raises(StyleValidationError, match="entrance.duration_ms"):
        Style.from_dict({"name": "X", "entrance": {"duration_ms": -5}})


def test_rejects_out_of_range_max_words():
    with pytest.raises(StyleValidationError, match="layout.max_words"):
        Style.from_dict({"name": "X", "layout": {"max_words": 0}})


def test_rejects_non_bool_uppercase():
    with pytest.raises(StyleValidationError, match="uppercase"):
        Style.from_dict({"name": "X", "uppercase": "yes"})


def test_missing_font_is_named_in_the_error():
    with pytest.raises(StyleValidationError) as excinfo:
        Style.from_dict({"name": "X", "font": "Comic Sans MS"})
    message = str(excinfo.value)
    assert "Comic Sans MS" in message
    assert "font" in message


def test_check_font_false_skips_manifest_lookup():
    # Used by tests of the rest of the schema that don't want a manifest
    # dependency -- library.py always leaves this on.
    style = Style.from_dict({"name": "X", "font": "Not A Real Font"}, check_font=False)
    assert style.font == "Not A Real Font"


def test_to_dict_round_trips_through_from_dict():
    original = Style.from_dict(
        {
            "name": "ROUNDTRIP",
            "font": "Poppins",
            "active_word": {"effect": "shake", "scale": 1.3},
            "layout": {"position": "top"},
        }
    )
    rebuilt = Style.from_dict(original.to_dict())
    assert rebuilt == original
