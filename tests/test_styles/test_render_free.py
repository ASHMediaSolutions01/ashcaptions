"""``render_free.free_events`` -- one event per word, each at its own
slot, all ending together (design 2026-09-05, section 5).

The behaviour worth naming: a free event ends at the **card's** end, not
when the next word starts. That is the whole difference between words
that accumulate on screen and words that replace each other, and it is
the first thing tested here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.render import render_ass
from ash_captions.styles.render_free import SETTLE_DROP_PX, free_events
from ash_captions.styles.schema import Style

PLAY_RES = (1080, 1920)

FREE_STYLE = {
    "name": "FIX FREE",
    "font": "Anton",
    "size": 90,
    "colors": {"text": "#FFFFFF", "active": "#FFD400", "outline": "#0A0A0A"},
    "active_word": {"effect": "none"},
    "entrance": {"effect": "none", "duration_ms": 0},
    "exit": {"effect": "fade", "duration_ms": 160},
    "layout": {
        "mode": "free",
        "position": "center",
        "max_words": 4,
        "slots": [
            {"x": 0.25, "y": 0.30, "scale": 0.50, "role": "text", "italic": True,
             "font": "Poppins", "entrance": "fade_settle"},
            {"x": 0.50, "y": 0.50, "scale": 2.00, "role": "active", "entrance": "stretch_collapse"},
            {"x": 0.40, "y": 0.70, "scale": 1.00, "role": "outline", "entrance": "stretch_collapse"},
            {"x": 0.75, "y": 0.85, "scale": 0.60, "role": "text", "italic": True, "entrance": "none"},
        ],
    },
}


@dataclass(frozen=True)
class FakeWordStyle:
    """The two fields ``free_events`` reads off track B's ``WordStyle``
    (design 2026-09-05, "Interfaces"). Defined here so this track's tests
    do not wait on that dataclass landing."""

    x: float | None = None
    y: float | None = None


def _style(**overrides) -> Style:
    definition = {**FREE_STYLE, **overrides}
    return Style.from_dict(definition, check_font=False)


def _card() -> Card:
    words = (
        Word(text="the", start=0.0, end=0.30),
        Word(text="2nd", start=0.30, end=0.70),
        Word(text="Highest", start=0.70, end=1.20),
        Word(text="residential", start=1.20, end=1.90),
    )
    return Card(words=words, start=0.0, end=1.90)


def _events(style: Style | None = None, **kwargs) -> list[str]:
    return free_events(_card(), style or _style(), "FIX_FREE", *PLAY_RES, **kwargs)


def _by_word(events: list[str]) -> dict[str, str]:
    return {event.rsplit("}", 1)[1]: event for event in events}


def _tag(event: str, name: str) -> str | None:
    match = re.search(rf"\\{name}\(([^)]*)\)", event)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# the events themselves
# ---------------------------------------------------------------------------


def test_one_event_per_word():
    events = _events()
    assert len(events) == 4
    assert sorted(_by_word(events)) == ["2nd", "Highest", "residential", "the"]


def test_every_event_ends_when_the_card_ends_so_words_accumulate():
    for event in _events():
        assert event.split(",")[2] == "0:00:01.90"


def test_each_event_starts_when_its_own_word_starts():
    starts = {word: event.split(",")[1] for word, event in _by_word(_events()).items()}
    assert starts["the"] == "0:00:00.00"
    assert starts["2nd"] == "0:00:00.30"
    assert starts["Highest"] == "0:00:00.70"
    assert starts["residential"] == "0:00:01.20"


def test_a_word_is_centred_on_its_slot_point_in_playres_pixels():
    events = _by_word(_events())
    assert "\\an5" in events["2nd"]
    assert _tag(events["2nd"], "pos") == "540,960"  # 0.50 x 1080, 0.50 x 1920
    assert _tag(events["Highest"], "pos") == "432,1344"  # 0.40 x 1080, 0.70 x 1920


def test_the_slot_carries_scale_colour_font_and_lean():
    events = _by_word(_events())
    big, small = events["2nd"], events["the"]
    assert "\\fscy200" in big and "\\c&H00D4FF&" in big and "\\fnAnton" in big
    assert "\\i1" not in big
    assert "\\fscy55" in small  # 50 x the 1.1 fade-settle entrance scale
    assert "\\c&HFFFFFF&" in small and "\\fnPoppins" in small and "\\i1" in small
    assert "\\c&H0A0A0A&" in events["Highest"]  # role: outline


def test_the_border_scales_with_the_slot_and_can_be_switched_off():
    """libass does not scale the border with ``\\fscx``, so a free event
    emits its own ``\\bord``; a slot drawn in the look's outline colour
    needs to switch it off or it renders as a slab."""
    # outline_width for size 90 is round(90 * 0.055) = 5.
    events = _by_word(_events())
    assert "\\bord10" in events["2nd"]  # 5 x the 2.00 slot
    assert "\\bord2" in events["the"]  # 5 x 0.50, banker-rounded from 2.5
    slots = FREE_STYLE["layout"]["slots"]
    off = _style(layout={**FREE_STYLE["layout"], "slots": [{**slots[0], "border": 0.0}, *slots[1:]]})
    assert "\\bord0" in _by_word(_events(off))["the"]


def test_a_line_mode_look_never_reaches_the_free_renderer():
    line = Style.from_dict({"name": "LINE", "font": "Anton"}, check_font=False)
    rendered = render_ass([_card()], line, play_res=PLAY_RES)
    assert "\\an5" not in rendered
    assert len([ln for ln in rendered.splitlines() if ln.startswith("Dialogue:")]) == 4
    # ...and the free look goes through render_ass to the same place.
    assert "\\an5" in render_ass([_card()], _style(), play_res=PLAY_RES)


# ---------------------------------------------------------------------------
# the two entrances
# ---------------------------------------------------------------------------


def test_stretch_collapse_enters_wide_and_snaps_back_over_120ms():
    event = _by_word(_events())["2nd"]
    assert "\\fscx360\\fscy200" in event  # 180% of the slot's 200%, height unchanged
    assert "\\t(0,120,\\fscx200)" in event


def test_fade_settle_shrinks_and_drops_over_240ms():
    event = _by_word(_events())["the"]
    assert "\\fscx55\\fscy55" in event  # 110% of the slot's 50%
    assert "\\t(0,240,\\fscx50\\fscy50)" in event
    x, y0, x2, y2, t0, t1 = _tag(event, "move").split(",")
    assert (x, x2, t0, t1) == ("270", "270", "0", "240")
    assert float(y2) - float(y0) == SETTLE_DROP_PX  # it drops into place


def test_a_settling_word_is_placed_by_its_move_not_by_pos():
    """``\\pos`` and ``\\move`` do not compose in libass."""
    event = _by_word(_events())["the"]
    assert "\\move(" in event and "\\pos(" not in event


def test_entrance_none_is_a_plain_scale_at_a_plain_position():
    event = _by_word(_events())["residential"]
    assert "\\fscx60\\fscy60" in event
    assert "\\t(" not in event
    assert _tag(event, "pos") == "810,1632"


def test_entrance_and_exit_merge_into_one_fad():
    events = _by_word(_events())
    assert "\\fad(120,160)" in events["2nd"]
    assert "\\fad(240,160)" in events["the"]
    assert "\\fad(0,160)" in events["residential"]  # exit only
    assert events["2nd"].count("\\fad(") == 1


def test_a_look_with_no_exit_fades_in_only():
    style = _style(exit={"effect": "none", "duration_ms": 0})
    assert "\\fad(120,0)" in _by_word(_events(style))["2nd"]


def test_both_halves_are_clamped_to_a_short_event():
    """A word arriving 100ms before the card ends cannot carry a 120ms
    entrance and a 160ms exit."""
    words = (Word(text="quick", start=0.0, end=0.10),)
    card = Card(words=words, start=0.0, end=0.10)
    event = free_events(card, _style(), "FIX_FREE", *PLAY_RES)[0]
    entrance, exit_ = (int(part) for part in _tag(event, "fad").split(","))
    assert entrance + exit_ <= 100


# ---------------------------------------------------------------------------
# the drag offset and the per-word override
# ---------------------------------------------------------------------------


def test_the_anchor_offset_shifts_every_slot_by_the_same_delta():
    events = _by_word(_events(offset=(60.0, -100.0)))
    assert _tag(events["2nd"], "pos") == "600,860"
    assert _tag(events["Highest"], "pos") == "492,1244"


def test_the_studio_drag_reaches_the_free_renderer_through_render_ass():
    undragged = render_ass([_card()], _style(), play_res=PLAY_RES)
    dragged = render_ass([_card()], _style(), play_res=PLAY_RES, anchor=(540.0, 860.0))
    assert "\\pos(540,960)" in undragged
    assert "\\pos(540,860)" in dragged  # the whole cluster moved up by 100


@pytest.mark.parametrize(
    "override,expected",
    [
        (FakeWordStyle(x=0.10, y=0.90), "108,1728"),
        (FakeWordStyle(x=0.10), "108,960"),  # y falls back to the slot
        (FakeWordStyle(y=0.90), "540,1728"),
    ],
)
def test_a_word_may_override_its_own_position(override, expected):
    word_styles = {(0.30, 0.70): override}
    events = _by_word(_events(word_styles=word_styles))
    assert _tag(events["2nd"], "pos") == expected
    assert _tag(events["Highest"], "pos") == "432,1344"  # its neighbour is untouched


def test_an_overridden_word_is_placed_absolutely_not_relative_to_the_drag():
    word_styles = {(0.30, 0.70): FakeWordStyle(x=0.10, y=0.90)}
    events = _by_word(_events(offset=(60.0, -100.0), word_styles=word_styles))
    assert _tag(events["2nd"], "pos") == "108,1728"
    assert _tag(events["Highest"], "pos") == "492,1244"  # the rest still moved


def test_an_empty_word_styles_mapping_changes_nothing():
    assert _events(word_styles={}) == _events()


# ---------------------------------------------------------------------------
# escaping and case still apply
# ---------------------------------------------------------------------------


def test_braces_and_backslashes_are_escaped_like_every_other_renderer():
    words = (Word(text="{name}", start=0.0, end=0.4), Word(text="a\\b", start=0.4, end=0.8))
    card = Card(words=words, start=0.0, end=0.8)
    events = free_events(card, _style(), "FIX_FREE", *PLAY_RES)
    bodies = [event.rsplit("}", 1)[1] for event in events]
    assert bodies == ["｛name｝", "a＼b"]


def test_uppercase_applies():
    style = _style(uppercase=True)
    assert "2ND" in "".join(_events(style))


# ---------------------------------------------------------------------------
# the intensity dial
# ---------------------------------------------------------------------------


def _at(intensity: float) -> dict[str, str]:
    return _by_word(_events(_style(layout={**FREE_STYLE["layout"], "intensity": intensity})))


def test_intensity_1_is_the_look_as_its_author_drew_it():
    assert _at(1.0) == _by_word(_events())


def test_intensity_0_is_a_tidy_stack_on_the_centre_column_at_one_size():
    events = _at(0.0)
    for word in ("2nd", "Highest", "residential"):
        assert _tag(events[word], "pos").startswith("540,"), (word, events[word])
    # 540 is 0.50 x 1080 whatever the slot's own x was.
    assert _tag(events["the"], "move").startswith("540,")
    # Every word settles at the look's own size. The entrances still run --
    # intensity is about spread and size, not about whether words animate --
    # so it is the *settled* scale that flattens, not the tag it starts on.
    assert "\\t(0,120,\\fscx100)" in events["2nd"]  # the 2.00 slot flattened
    assert "\\t(0,120,\\fscx100)" in events["Highest"]
    assert "\\t(0,240,\\fscx100\\fscy100)" in events["the"]
    assert "\\fscx100\\fscy100" in events["residential"]  # entrance "none"


def test_intensity_leaves_the_vertical_alone_so_words_never_stack_up():
    """Only x and scale move; collapsing y would put two words on one
    point, which is the thing the slot list exists to prevent."""
    for word in ("2nd", "Highest", "residential"):
        full, flat = _tag(_at(1.0)[word], "pos"), _tag(_at(0.0)[word], "pos")
        assert full.split(",")[1] == flat.split(",")[1], word


def test_intensity_interpolates_both_the_spread_and_the_sizes():
    half = _at(0.5)
    # slot 2 sits at x=0.40 (432 px); halfway to the centre column is 486.
    assert _tag(half["Highest"], "pos") == "486,1344"
    # ...and its 2.00x neighbour is halfway back to 100%.
    assert "\\fscy150" in half["2nd"]


def test_intensity_does_not_change_which_word_gets_which_slot():
    """The dial changes how dramatic a layout is, never its assignment --
    so it is applied after assign_slots, not before."""
    for intensity in (1.0, 0.6, 0.0):
        events = _at(intensity)
        assert "\\i1" in events["the"] and "\\i1" not in events["2nd"]
        assert "\\c&H00D4FF&" in events["2nd"]  # still the active-role slot
        assert "\\c&H0A0A0A&" in events["Highest"]  # still the outline-role slot


def test_intensity_scales_the_border_with_the_size_it_scales():
    assert "\\bord10" in _at(1.0)["2nd"]  # 5 x 2.00
    assert "\\bord5" in _at(0.0)["2nd"]  # 5 x 1.00
