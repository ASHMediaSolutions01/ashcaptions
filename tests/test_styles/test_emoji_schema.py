"""The ``emoji`` block on a style (v0.9).

Emoji bursts were three fields in settings.json until v0.9, which is
where punch-in's keywords started too. They belong to the look for the
reason sound does -- "REEL POP with the fire emoji" is not a different
look from "REEL POP" -- so these are the tests that a look without one is
unchanged, a look with one round-trips through the style editor, and a
mistake in the editor comes back naming the field that was wrong.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ash_captions.styles.schema import (
    EMOJI_TRIGGERS,
    EmojiBursts,
    Style,
    StyleValidationError,
)


def style(emoji: dict | None = None, **extra) -> Style:
    data = {"name": "T", **extra}
    if emoji is not None:
        data["emoji"] = emoji
    return Style.from_dict(data, check_font=False)


# ---------------------------------------------------------------------------
# the default: nothing, everywhere
# ---------------------------------------------------------------------------


def test_a_look_that_says_nothing_about_emoji_throws_none():
    assert style().emoji == EmojiBursts()
    assert style().emoji.trigger == "off"
    assert style().emoji.enabled is False


def test_every_shipped_look_is_bare_until_someone_asks():
    """The feature is opt-in per look. A shipped look arriving with a
    trigger set would put emoji on six editors' back catalogue the next
    time it is re-burned."""
    shipped = Path(__file__).resolve().parents[2] / "styles"
    seen = 0
    for path in sorted(shipped.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("emoji", {}).get("trigger", "off") == "off", path.name
        seen += 1
    assert seen, "no shipped looks were checked"


# ---------------------------------------------------------------------------
# what the editor may save
# ---------------------------------------------------------------------------


def test_a_look_round_trips_through_to_dict():
    original = style({"trigger": "keyword", "emoji": ["fire", "star"], "min_spacing_seconds": 4.0})
    again = Style.from_dict(original.to_dict(), check_font=False)
    assert again.emoji == original.emoji
    assert again.emoji.emoji == ("fire", "star")


def test_to_dict_writes_a_list_not_a_tuple():
    """It is JSON that gets written; a tuple would not survive."""
    assert style({"trigger": "off", "emoji": []}).to_dict()["emoji"]["emoji"] == []


def test_the_order_picked_is_the_order_kept():
    """The cycle is the pick order -- two emoji alternate -- so it is
    data, not a set."""
    assert style({"trigger": "sentence", "emoji": ["star", "fire"]}).emoji.emoji == ("star", "fire")


def test_four_is_the_most_a_look_may_cycle():
    with pytest.raises(StyleValidationError) as exc:
        style({"trigger": "sentence", "emoji": ["a", "b", "c", "d", "e"]})
    assert "more than the 4" in str(exc.value)


def test_a_trigger_with_nothing_to_throw_is_refused_by_name():
    with pytest.raises(StyleValidationError) as exc:
        style({"trigger": "sentence", "emoji": []})
    assert "emoji.trigger" in str(exc.value)


def test_off_with_nothing_picked_is_fine():
    assert style({"trigger": "off", "emoji": []}).emoji.enabled is False


def test_an_unknown_trigger_names_the_field_and_the_choices():
    with pytest.raises(StyleValidationError) as exc:
        style({"trigger": "every_word", "emoji": ["fire"]})
    assert "emoji.trigger" in str(exc.value)


def test_there_is_no_every_word_trigger():
    """A sound on every word is a rhythm you can defend on a one-word
    look. A picture on every word is confetti, and the engine has no
    such trigger to offer."""
    assert "word" not in EMOJI_TRIGGERS


def test_an_unknown_field_is_refused_rather_than_ignored():
    with pytest.raises(StyleValidationError) as exc:
        style({"trigger": "off", "size": 3})
    assert "unknown field" in str(exc.value)


def test_a_name_that_is_not_a_string_names_its_index():
    with pytest.raises(StyleValidationError) as exc:
        style({"trigger": "sentence", "emoji": ["fire", 7]})
    assert "emoji.emoji[1]" in str(exc.value)


@pytest.mark.parametrize("spacing", [0.1, 0.0, -1, 61])
def test_a_gap_outside_the_range_is_refused(spacing):
    with pytest.raises(StyleValidationError) as exc:
        style({"trigger": "off", "min_spacing_seconds": spacing})
    assert "emoji.min_spacing_seconds" in str(exc.value)


def test_the_default_gap_is_wider_than_the_sounds_one():
    """A noise every third of a second is a rhythm; a picture every third
    of a second is a mess."""
    from ash_captions.styles.schema import Sound

    assert EmojiBursts().min_spacing_seconds > Sound().min_spacing_seconds


# ---------------------------------------------------------------------------
# in step with the engine
# ---------------------------------------------------------------------------


def test_every_trigger_the_schema_allows_is_one_the_engine_fires():
    """``EMOJI_TRIGGERS`` is duplicated rather than imported, because
    styles must not depend on engine. This is what keeps the copy
    honest."""
    from ash_captions.engine.stickers import select_bursts
    from ash_captions.engine.transcribe import Word

    words = [Word("free", 0.0, 0.3), Word("free", 9.0, 9.3)]
    for trigger in EMOJI_TRIGGERS:
        out = select_bursts(
            words, trigger=trigger, emoji=["fire"], keywords=["free"], min_spacing=1.0
        )
        if trigger == "off":
            assert out == []
        else:
            assert out, trigger


def test_an_emoji_this_build_does_not_ship_is_refused():
    from ash_captions.styles.emoji import list_emoji

    if not list_emoji():
        pytest.skip("this checkout has no emoji; nothing to validate against")
    with pytest.raises(StyleValidationError) as exc:
        Style.from_dict(
            {"name": "T", "emoji": {"trigger": "sentence", "emoji": ["nope"]}}, check_font=True
        )
    assert "not a bundled emoji" in str(exc.value)


def test_a_checkout_with_no_emoji_does_not_make_every_look_unloadable(monkeypatch):
    """The rule ``_reject_unbundled_sounds`` already follows: a fresh
    checkout that has not run scripts/fetch_emoji.py must not turn every
    look carrying an emoji into an error. The burn already treats an
    emoji it cannot find on disk as no emoji at all."""
    import ash_captions.styles.emoji as emoji_module

    monkeypatch.setattr(emoji_module, "list_emoji", lambda **kw: ())
    look = Style.from_dict(
        {"name": "T", "emoji": {"trigger": "sentence", "emoji": ["whatever"]}}, check_font=True
    )
    assert look.emoji.emoji == ("whatever",)
