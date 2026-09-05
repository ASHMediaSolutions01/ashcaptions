"""``layout.mode`` and ``layout.slots`` -- the free-placement schema
(design 2026-09-05, section 5).

The load-bearing default is that ``mode`` is ``"line"``: a look that says
nothing about free placement is the look it was before v0.6, and every
shipped style file stays valid untouched. Everything else here is the
same standard the rest of ``schema.py`` holds itself to -- an error
naming the exact field at fault, including the index of the offending
slot.
"""
from __future__ import annotations

import json

import pytest

from ash_captions.styles.schema import (
    FREE_ENTRANCES,
    LAYOUT_MODES,
    SLOT_COLOUR_ROLES,
    Layout,
    Slot,
    Style,
    StyleValidationError,
)

FREE_LAYOUT = {
    "mode": "free",
    "position": "center",
    "max_words": 3,
    "slots": [
        {"x": 0.30, "y": 0.34, "scale": 0.55, "role": "text", "italic": True,
         "font": "Poppins", "entrance": "fade_settle"},
        {"x": 0.53, "y": 0.46, "scale": 1.70, "role": "active", "entrance": "stretch_collapse"},
        {"x": 0.44, "y": 0.58, "scale": 1.10, "role": "outline"},
    ],
}


def _free(**overrides) -> dict:
    layout = json.loads(json.dumps(FREE_LAYOUT))
    layout.update(overrides)
    return {"name": "FREE", "layout": layout}


# ---------------------------------------------------------------------------
# defaults: every look shipped before v0.6 is untouched
# ---------------------------------------------------------------------------


def test_a_layout_that_says_nothing_is_line_mode_with_no_slots():
    layout = Layout.from_dict({})
    assert layout.mode == "line"
    assert layout.slots == ()
    assert Style.from_dict({"name": "X"}).layout.mode == "line"


def test_an_existing_layout_block_still_parses_unchanged():
    layout = Layout.from_dict({"position": "top", "max_words": 3, "align": "left", "margin_v": 140})
    assert (layout.position, layout.max_words, layout.align, layout.mode) == ("top", 3, "left", "line")


# ---------------------------------------------------------------------------
# a free layout
# ---------------------------------------------------------------------------


def test_a_full_free_layout_parses():
    style = Style.from_dict(_free())
    layout = style.layout
    assert layout.mode == "free"
    assert len(layout.slots) == 3
    assert layout.slots[0] == Slot(
        x=0.30, y=0.34, scale=0.55, role="text", italic=True, font="Poppins", entrance="fade_settle"
    )
    # Slot defaults fill in the rest.
    assert layout.slots[2].italic is False
    assert layout.slots[2].font is None
    assert layout.slots[2].entrance == "stretch_collapse"
    assert layout.slots[2].border == 1.0


def test_a_slot_needs_both_x_and_y():
    for missing in ("x", "y"):
        slot = {"x": 0.5, "y": 0.5}
        del slot[missing]
        with pytest.raises(StyleValidationError, match=rf"layout\.slots\[0\]\.{missing}"):
            Style.from_dict(_free(slots=[slot], max_words=1))


@pytest.mark.parametrize(
    "slot,path",
    [
        ({"x": 1.4, "y": 0.5}, r"layout\.slots\[0\]\.x"),
        ({"x": 0.5, "y": -0.1}, r"layout\.slots\[0\]\.y"),
        ({"x": "half", "y": 0.5}, r"layout\.slots\[0\]\.x"),
        ({"x": 0.5, "y": 0.5, "scale": 4.0}, r"layout\.slots\[0\]\.scale"),
        ({"x": 0.5, "y": 0.5, "scale": 0.05}, r"layout\.slots\[0\]\.scale"),
        ({"x": 0.5, "y": 0.5, "role": "primary"}, r"layout\.slots\[0\]\.role"),
        ({"x": 0.5, "y": 0.5, "italic": "yes"}, r"layout\.slots\[0\]\.italic"),
        ({"x": 0.5, "y": 0.5, "entrance": "explode"}, r"layout\.slots\[0\]\.entrance"),
        ({"x": 0.5, "y": 0.5, "font": ""}, r"layout\.slots\[0\]\.font"),
        ({"x": 0.5, "y": 0.5, "border": 4.0}, r"layout\.slots\[0\]\.border"),
        ({"x": 0.5, "y": 0.5, "border": -0.5}, r"layout\.slots\[0\]\.border"),
        ({"x": 0.5, "y": 0.5, "wobble": 1}, r"layout\.slots\[0\]"),
    ],
)
def test_each_bad_slot_field_names_its_own_path(slot, path):
    with pytest.raises(StyleValidationError, match=path):
        Style.from_dict(_free(slots=[slot], max_words=1))


def test_the_offending_slot_index_is_in_the_message():
    slots = [{"x": 0.2, "y": 0.2}, {"x": 0.4, "y": 0.4}, {"x": 0.6, "y": 9.0}]
    with pytest.raises(StyleValidationError, match=r"layout\.slots\[2\]\.y"):
        Style.from_dict(_free(slots=slots))


def test_a_slot_font_must_be_bundled_unless_the_font_check_is_off():
    bad = _free(slots=[{"x": 0.5, "y": 0.5, "font": "Comic Sans MS"}], max_words=1)
    with pytest.raises(StyleValidationError, match=r"layout\.slots\[0\]\.font.*bundled"):
        Style.from_dict(bad)
    assert Style.from_dict(bad, check_font=False).layout.slots[0].font == "Comic Sans MS"


def test_an_unknown_mode_is_rejected():
    with pytest.raises(StyleValidationError, match="layout.mode"):
        Style.from_dict(_free(mode="scattered"))


# ---------------------------------------------------------------------------
# the two cross-field rules
# ---------------------------------------------------------------------------


def test_free_mode_needs_at_least_one_slot():
    with pytest.raises(StyleValidationError, match=r"layout\.slots.*at least one slot"):
        Style.from_dict(_free(slots=[], max_words=1))


def test_slots_are_rejected_on_a_line_layout():
    with pytest.raises(StyleValidationError, match=r'layout\.slots.*mode "free"'):
        Style.from_dict(_free(mode="line"))


def test_a_free_layout_needs_as_many_slots_as_max_words():
    """Fewer slots than max_words would stack two words on one point."""
    with pytest.raises(StyleValidationError, match=r"layout\.slots.*max_words=4"):
        Style.from_dict(_free(max_words=4))
    assert len(Style.from_dict(_free(max_words=3)).layout.slots) == 3


def test_more_slots_than_the_ceiling_is_rejected():
    many = [{"x": 0.1, "y": 0.1} for _ in range(9)]
    with pytest.raises(StyleValidationError, match=r"layout\.slots.*maximum"):
        Style.from_dict(_free(slots=many))


def test_slots_must_be_a_list():
    with pytest.raises(StyleValidationError, match=r"layout\.slots.*expected a list"):
        Style.from_dict(_free(slots={"x": 0.5, "y": 0.5}))


# ---------------------------------------------------------------------------
# round-trip: the style editor saves what it loaded
# ---------------------------------------------------------------------------


def test_to_dict_round_trips_a_free_layout_through_json():
    style = Style.from_dict(_free())
    wire = json.loads(json.dumps(style.to_dict()))
    assert wire["layout"]["mode"] == "free"
    assert wire["layout"]["slots"][0]["font"] == "Poppins"
    assert Style.from_dict(wire).layout == style.layout


def test_to_dict_round_trips_a_line_layout():
    style = Style.from_dict({"name": "PLAIN", "layout": {"position": "top"}})
    wire = json.loads(json.dumps(style.to_dict()))
    assert wire["layout"]["mode"] == "line"
    assert wire["layout"]["slots"] == []
    assert Style.from_dict(wire).layout == style.layout


def test_the_enums_are_what_the_renderer_branches_on():
    assert LAYOUT_MODES == {"line", "free"}
    assert FREE_ENTRANCES == {"none", "stretch_collapse", "fade_settle"}
    # Every role names a real key of the look's own colors block.
    assert SLOT_COLOUR_ROLES == {"text", "active", "outline", "shadow", "box"}
    from ash_captions.styles.schema import Colors

    for role in SLOT_COLOUR_ROLES:
        assert hasattr(Colors(), role)
