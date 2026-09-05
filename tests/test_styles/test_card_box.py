"""card_box boxes the whole caption, not one word at a time."""
from __future__ import annotations

from ash_captions.engine import Card, Word
from ash_captions.styles.render import render_ass
from ash_captions.styles.schema import Style


def _card():
    words = (Word("Breaking", 0.0, 0.4), Word("news", 0.4, 0.8), Word("tonight", 0.8, 1.3))
    return Card(words=words, start=0.0, end=1.3)


def test_card_box_puts_the_bar_on_the_base_style_and_keeps_the_whole_caption():
    style = Style.from_dict({"name": "NEWSY", "font": "Inter", "colors": {"box": "#0B2545EE"},
                             "active_word": {"effect": "card_box"}, "layout": {"position": "lower_third", "align": "left"}})
    ass = render_ass([_card()], style, play_res=(1920, 1080))
    base = [line for line in ass.splitlines() if line.startswith("Style: NEWSY,")][0]
    fields = base.split(",")
    assert fields[15] == "3"  # BorderStyle: opaque box on the base style
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events, "no events"
    # every event carries all three words (the whole caption is on screen)
    assert all("Breaking" in e and "news" in e and "tonight" in e for e in events)
    assert fields[18] == "1"  # lower_third + left -> \an1


def test_box_effect_still_shows_one_word_at_a_time():
    style = Style.from_dict({"name": "POPPY", "font": "Inter", "active_word": {"effect": "box", "box": True}})
    ass = render_ass([_card()], style, play_res=(1080, 1920))
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 3
    assert not any("Breaking" in e and "news" in e for e in events)
