"""The ``sound`` block on a style (v0.7 section 1).

Sound belongs to the look, not to the job -- the same decision the font
carries -- so these are the tests that a look without one is unchanged, a
look with one round-trips through the style editor, and a mistake in the
editor comes back naming the field that was wrong.
"""
from __future__ import annotations

import json

import pytest

from ash_captions.styles.schema import (
    SOUND_TRIGGERS,
    Sound,
    Style,
    StyleValidationError,
)


def style(sound: dict | None = None, **extra) -> Style:
    data = {"name": "T", **extra}
    if sound is not None:
        data["sound"] = sound
    return Style.from_dict(data, check_font=False)


# ---------------------------------------------------------------------------
# the default: silence, everywhere
# ---------------------------------------------------------------------------


def test_a_look_that_says_nothing_about_sound_makes_none():
    assert style().sound == Sound()
    assert style().sound.trigger == "off"
    assert style().sound.enabled is False


def test_every_shipped_look_is_silent_until_someone_asks():
    """The whole feature is opt-in per look. If a shipped look ever
    arrives with a trigger set, six editors' back catalogue starts
    whooshing the next time it is re-burned."""
    from pathlib import Path

    shipped = Path(__file__).resolve().parents[2] / "styles"
    for path in sorted(shipped.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("sound", {}).get("trigger", "off") == "off", path.name


def test_an_empty_sound_list_with_a_trigger_is_rejected_by_name():
    """Silence with the switch on is the confusing failure: the Styles
    page would say a look fires sounds and nothing would happen."""
    with pytest.raises(StyleValidationError, match="sound.trigger is 'sentence'"):
        style({"trigger": "sentence"})


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_the_triggers_are_the_ones_the_engine_implements():
    from ash_captions.engine.sfx import SfxTrigger

    assert SOUND_TRIGGERS == {t.value for t in SfxTrigger}


@pytest.mark.parametrize("trigger", sorted(SOUND_TRIGGERS - {"off"}))
def test_each_trigger_is_accepted(trigger):
    assert style({"trigger": trigger, "sounds": ["pop"]}).sound.trigger == trigger


def test_an_unknown_trigger_names_itself_and_the_alternatives():
    with pytest.raises(StyleValidationError, match="sound.trigger"):
        style({"trigger": "whenever", "sounds": ["pop"]})


def test_more_than_four_sounds_is_rejected():
    """Past four a cycling list stops reading as a pattern."""
    with pytest.raises(StyleValidationError, match="more than the 4"):
        style({"trigger": "word", "sounds": ["pop"] * 5})


def test_a_sound_the_manifest_does_not_list_is_rejected_with_the_choices():
    with pytest.raises(StyleValidationError, match="not a bundled sound"):
        Style.from_dict({"name": "T", "sound": {"trigger": "word", "sounds": ["airhorn"]}})


def test_an_absent_library_accepts_any_name_rather_than_breaking_every_look(monkeypatch):
    """An old bundle, or a checkout that has not generated the sounds.
    Rejecting then would make a look carrying sound unloadable, which is
    a much worse failure than one that burns without it -- and the burn
    already drops a sound it cannot find on disk."""
    monkeypatch.setattr("ash_captions.styles.sounds.list_sound_names", lambda **_: ())
    assert Style.from_dict(
        {"name": "T", "sound": {"trigger": "word", "sounds": ["airhorn"]}}
    ).sound.sounds == ("airhorn",)


@pytest.mark.parametrize(
    ("field", "value"),
    [("gain_db", -99), ("gain_db", 40), ("offset_ms", -900), ("offset_ms", 900),
     ("min_spacing_seconds", 0.0), ("min_spacing_seconds", 600)],
)
def test_out_of_range_numbers_are_rejected_naming_the_field(field, value):
    with pytest.raises(StyleValidationError, match=f"sound.{field}"):
        style({"trigger": "word", "sounds": ["pop"], field: value})


def test_an_unknown_field_inside_sound_is_rejected():
    with pytest.raises(StyleValidationError, match="unknown field"):
        style({"trigger": "word", "sounds": ["pop"], "volume": 3})


def test_sounds_must_be_a_list_of_names():
    with pytest.raises(StyleValidationError, match="sound.sounds"):
        style({"trigger": "word", "sounds": "pop"})
    with pytest.raises(StyleValidationError, match=r"sound.sounds\[0\]"):
        style({"trigger": "word", "sounds": [3]})


# ---------------------------------------------------------------------------
# the style editor's round trip
# ---------------------------------------------------------------------------


def test_a_sound_survives_to_dict_and_back():
    original = style({"trigger": "keyword", "sounds": ["pop", "whoosh"],
                      "gain_db": -12.5, "offset_ms": -80, "min_spacing_seconds": 1.5})
    assert Style.from_dict(original.to_dict(), check_font=False) == original


def test_to_dict_always_carries_the_block_so_the_editor_can_show_it():
    assert style().to_dict()["sound"] == {
        "trigger": "off", "sounds": [], "gain_db": -8.0,
        "offset_ms": 0, "min_spacing_seconds": 0.35,
    }


def test_enabled_needs_both_a_trigger_and_a_sound():
    assert Sound(trigger="word", sounds=("pop",)).enabled is True
    assert Sound(trigger="off", sounds=("pop",)).enabled is False
    assert Sound(trigger="word", sounds=()).enabled is False
