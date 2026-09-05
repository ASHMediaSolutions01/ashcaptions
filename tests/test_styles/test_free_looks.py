"""The three shipped free-placement looks (design 2026-09-05, section 5).

They are data, nothing else: three JSON files in ``styles/`` that the
library picks up, the Studio lists and the renderer draws with no code
knowing their names -- the same contract every other look has.
"""
from __future__ import annotations

import json

import pytest

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.library import list_styles, load_style_file, shipped_styles_dir
from ash_captions.styles.render import render_ass

FREE_LOOKS = {
    "REEL ESTATE": "reel_estate.json",
    "QUIET SPLIT": "quiet_split.json",
    "BIG NUMBER": "big_number.json",
}
PLAY_RES = (1080, 1920)


@pytest.fixture(scope="module")
def shipped():
    """Only the shipped looks -- a user override directory would make
    this test depend on the machine it runs on."""
    return list_styles(user_dir=shipped_styles_dir().parent / "no-user-styles-here")


def _card(*texts: str) -> Card:
    words = tuple(
        Word(text=text, start=0.4 * i, end=0.4 * (i + 1)) for i, text in enumerate(texts)
    )
    return Card(words=words, start=words[0].start, end=words[-1].end)


@pytest.mark.parametrize("name,filename", sorted(FREE_LOOKS.items()))
def test_each_look_validates_with_the_font_check_on(name, filename):
    style = load_style_file(shipped_styles_dir() / filename)
    assert style.name == name
    assert style.layout.mode == "free"


@pytest.mark.parametrize("name", sorted(FREE_LOOKS))
def test_each_look_appears_in_the_looks_list(name, shipped):
    assert name in shipped


@pytest.mark.parametrize("name", sorted(FREE_LOOKS))
def test_each_look_has_a_slot_for_every_word_a_card_can_hold(name, shipped):
    layout = shipped[name].layout
    assert len(layout.slots) >= layout.max_words


@pytest.mark.parametrize("name", sorted(FREE_LOOKS))
def test_each_look_renders_one_event_per_word(name, shipped):
    style = shipped[name]
    card = _card(*["word"] * style.layout.max_words)
    events = [
        line for line in render_ass([card], style, play_res=PLAY_RES).splitlines()
        if line.startswith("Dialogue:")
    ]
    assert len(events) == style.layout.max_words
    assert all("\\pos(" in line or "\\move(" in line for line in events)


@pytest.mark.parametrize("name", sorted(FREE_LOOKS))
def test_each_look_uses_more_than_one_treatment(name, shipped):
    """A free look whose slots were all the same size would be a line
    look with extra steps."""
    slots = shipped[name].layout.slots
    assert len({slot.scale for slot in slots}) >= 2
    assert len({slot.role for slot in slots}) >= 2


def test_reel_estate_puts_the_number_on_its_biggest_slot(shipped):
    """The reference phrase, through the shipped look, end to end."""
    style = shipped["REEL ESTATE"]
    card = _card("the", "2nd", "Highest", "residential")
    events = [
        line for line in render_ass([card], style, play_res=PLAY_RES).splitlines()
        if line.startswith("Dialogue:")
    ]
    biggest = max(style.layout.slots, key=lambda slot: slot.scale)
    number = next(line for line in events if line.endswith("2nd"))
    assert f"\\fscy{round(biggest.scale * 100)}" in number
    assert f"\\c&H{0xFFD400 & 0xFF:02X}{(0xFFD400 >> 8) & 0xFF:02X}{0xFFD400 >> 16:02X}&" in number


def test_the_shipped_free_files_round_trip_through_the_schema():
    for filename in FREE_LOOKS.values():
        path = shipped_styles_dir() / filename
        style = load_style_file(path)
        assert json.loads(json.dumps(style.to_dict()))["layout"]["mode"] == "free"


def test_the_looks_list_still_holds_every_line_look(shipped):
    line_looks = [name for name, style in shipped.items() if style.layout.mode == "line"]
    assert len(line_looks) >= 36
    assert len(shipped) == len(line_looks) + len(FREE_LOOKS)
