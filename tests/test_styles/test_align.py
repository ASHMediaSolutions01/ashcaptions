"""layout.align places captions left/centre/right within their band."""
from __future__ import annotations

import pytest

from ash_captions.styles.ass_format import ass_alignment
from ash_captions.styles.schema import Style, StyleValidationError


@pytest.mark.parametrize(
    "position, align, expected",
    [
        ("bottom", "left", 1), ("bottom", "center", 2), ("bottom", "right", 3),
        ("lower_third", "left", 1), ("center", "center", 5), ("center", "right", 6),
        ("top", "left", 7), ("top", "center", 8), ("top", "right", 9),
    ],
)
def test_numpad_alignment(position, align, expected):
    assert ass_alignment(position, align) == expected


def test_align_defaults_to_center_and_round_trips():
    style = Style.from_dict({"name": "X", "layout": {"position": "bottom", "align": "left"}})
    assert style.layout.align == "left"
    assert style.to_dict()["layout"]["align"] == "left"
    assert Style.from_dict({"name": "Y"}).layout.align == "center"


def test_bad_align_is_rejected():
    with pytest.raises(StyleValidationError):
        Style.from_dict({"name": "X", "layout": {"align": "middle"}})


def test_animation_anchor_follows_position_and_align():
    from ash_captions.styles.render import _anchor_xy

    def anchor(position, align):
        style = Style.from_dict({"name": "A", "layout": {"position": position, "align": align,
                                                           "margin_l": 90, "margin_r": 70, "margin_v": 130}})
        return _anchor_xy(style, 1080, 1920)

    assert anchor("top", "right") == (1010, 130)
    assert anchor("top", "left") == (90, 130)
    assert anchor("bottom", "left") == (90, 1790)
    assert anchor("bottom", "center") == (540, 1790)
    assert anchor("center", "center") == (540, 960)
    assert anchor("center", "right") == (1010, 960)
    assert anchor("lower_third", "left") == (90, 1790)
