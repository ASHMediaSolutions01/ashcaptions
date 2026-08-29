"""Tests that ash_captions.engine.writers.render_ass/write_ass dispatch
correctly between the legacy AssPreset path and the new Style path.

engine/writers.py's ownership moved to this package for the style work,
but tests/test_engine/test_writers.py (owned elsewhere) still asserts the
legacy AssPreset rendering byte-for-byte, and app/runner.py still passes
``engine.CLEAN``/``engine.POP`` (AssPreset instances) straight into
``write_ass``. These tests guard the seam: both call shapes must keep
working from the same public functions.
"""
from __future__ import annotations

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.engine.writers import CLEAN, POP, AssPreset, render_ass, write_ass
from ash_captions.styles.schema import Style


def word(text, start, end):
    return Word(text=text, start=start, end=end)


def card(words):
    return Card(words=tuple(words), start=words[0].start, end=words[-1].end)


def test_render_ass_still_accepts_the_legacy_asspreset():
    cards = [card([word("hi", 0.0, 0.5)])]
    out = render_ass(cards, CLEAN)
    assert f"Style: {CLEAN.name}," in out


def test_render_ass_accepts_a_new_style_object():
    style = Style.from_dict({"name": "NEWSTYLE"}, check_font=False)
    cards = [card([word("hi", 0.0, 0.5)])]
    out = render_ass(cards, style)
    assert "Style: NEWSTYLE," in out


def test_legacy_and_new_paths_produce_different_ass_shapes():
    # AssPreset -> one Style block; Style -> two (main + _BOX companion).
    cards = [card([word("hi", 0.0, 0.5)])]
    style = Style.from_dict({"name": "NEWSTYLE"}, check_font=False)

    legacy_out = render_ass(cards, POP)
    new_out = render_ass(cards, style)

    assert legacy_out.count("Style: ") == 1
    assert new_out.count("Style: ") == 2


def test_write_ass_dispatches_for_both_preset_kinds(tmp_path):
    cards = [card([word("hi", 0.0, 0.5)])]
    style = Style.from_dict({"name": "NEWSTYLE"}, check_font=False)

    legacy_path = write_ass(cards, tmp_path / "legacy.ass", CLEAN)
    styled_path = write_ass(cards, tmp_path / "styled.ass", style)

    assert legacy_path.read_text(encoding="utf-8") == render_ass(cards, CLEAN)
    assert styled_path.read_text(encoding="utf-8") == render_ass(cards, style)


def test_custom_asspreset_still_works_unmodified():
    custom = AssPreset(name="CUSTOM", font_name="Comic Sans MS", font_size=40)
    cards = [card([word("hi", 0.0, 0.5)])]
    out = render_ass(cards, custom)
    assert "Style: CUSTOM,Comic Sans MS,40," in out
