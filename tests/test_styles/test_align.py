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
