"""Tests for ash_captions.engine.writers.

Pure render_* functions are tested directly on their string output;
write_* wrappers are tested against tmp_path to confirm the file lands
with the same content.
"""
from __future__ import annotations

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Segment, Word
from ash_captions.engine.writers import (
    CLEAN,
    POP,
    AssPreset,
    render_ass,
    render_srt,
    render_txt,
    write_ass,
    write_srt,
    write_txt,
)


def word(text, start, end):
    return Word(text=text, start=start, end=end)


def card(words):
    return Card(words=tuple(words), start=words[0].start, end=words[-1].end)


# ---------------------------------------------------------------------------
# .srt
# ---------------------------------------------------------------------------


def test_render_srt_basic_format():
    cards = [
        card([word("Hello", 0.0, 0.4), word("there", 0.4, 0.9)]),
        card([word("World", 1.0, 1.6)]),
    ]
    out = render_srt(cards)

    expected = (
        "1\n"
        "00:00:00,000 --> 00:00:00,900\n"
        "Hello there\n"
        "\n"
        "2\n"
        "00:00:01,000 --> 00:00:01,600\n"
        "World\n"
    )
    assert out == expected


def test_render_srt_empty_cards_is_empty_string():
    assert render_srt([]) == ""


def test_render_srt_formats_hours_and_pads_correctly():
    cards = [card([word("Hi", 3725.123, 3725.987)])]  # 1h 2m 5.123s
    out = render_srt(cards)

    assert "01:02:05,123 --> 01:02:05,987" in out


def test_write_srt_writes_file(tmp_path):
    cards = [card([word("Hi", 0.0, 0.5)])]
    path = tmp_path / "sub" / "out.srt"

    result = write_srt(cards, path)

    assert result == path
    assert path.read_text(encoding="utf-8") == render_srt(cards)


# ---------------------------------------------------------------------------
# .txt
# ---------------------------------------------------------------------------


def test_render_txt_joins_segment_text_by_line():
    segments = [
        Segment(text="Hello there.", start=0.0, end=1.0),
        Segment(text="How are you?", start=1.5, end=3.0),
    ]
    assert render_txt(segments) == "Hello there.\nHow are you?\n"


def test_render_txt_skips_empty_segments():
    segments = [
        Segment(text="", start=0.0, end=0.1),
        Segment(text="Real text.", start=1.0, end=2.0),
    ]
    assert render_txt(segments) == "Real text.\n"


def test_render_txt_empty_is_empty_string():
    assert render_txt([]) == ""


def test_write_txt_writes_file(tmp_path):
    segments = [Segment(text="Hello.", start=0.0, end=1.0)]
    path = tmp_path / "out.txt"

    write_txt(segments, path)

    assert path.read_text(encoding="utf-8") == render_txt(segments)


# ---------------------------------------------------------------------------
# .ass
# ---------------------------------------------------------------------------


def test_render_ass_includes_script_info_and_style_sections():
    cards = [card([word("Hi", 0.0, 0.5)])]
    out = render_ass(cards, CLEAN)

    assert "[Script Info]" in out
    assert "[V4+ Styles]" in out
    assert "[Events]" in out
    assert "PlayResX: 1080" in out
    assert "PlayResY: 1920" in out
    assert f"Style: {CLEAN.name}," in out


def test_render_ass_respects_custom_play_res():
    cards = [card([word("Hi", 0.0, 0.5)])]
    out = render_ass(cards, CLEAN, play_res=(1920, 1080))

    assert "PlayResX: 1920" in out
    assert "PlayResY: 1080" in out


def test_render_ass_emits_one_dialogue_line_per_word():
    cards = [card([word("One", 0.0, 0.3), word("two", 0.3, 0.6), word("three", 0.6, 0.9)])]
    out = render_ass(cards, POP)

    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue_lines) == 3


def test_render_ass_highlights_active_word_and_resets_after():
    cards = [card([word("One", 0.0, 0.3), word("two", 0.3, 0.6)])]
    out = render_ass(cards, POP)

    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    first_line = dialogue_lines[0]

    r, g, b = POP.highlight_colour
    highlight_tag = f"{{\\c&H{b:02X}{g:02X}{r:02X}&}}"
    assert highlight_tag in first_line
    assert "One" in first_line and "two" in first_line


def test_render_ass_word_event_spans_to_next_word_start():
    cards = [card([word("One", 0.0, 0.3), word("two", 0.5, 0.9)])]
    out = render_ass(cards, POP)

    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    # First word's event should run 0:00:00.00 -> 0:00:00.50 (next word's start),
    # not stop short at its own end (0.30).
    assert dialogue_lines[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.50,")


def test_render_ass_last_word_event_spans_to_card_end():
    cards = [card([word("One", 0.0, 0.3), word("two", 0.5, 0.9)])]
    out = render_ass(cards, POP)

    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    assert dialogue_lines[-1].startswith("Dialogue: 0,0:00:00.50,0:00:00.90,")


def test_render_ass_formats_hours_correctly():
    cards = [card([word("Hi", 3661.0, 3662.0)])]  # 1h 1m 1s
    out = render_ass(cards, CLEAN)

    assert "1:01:01.00" in out


def test_render_ass_empty_cards_has_no_dialogue_lines():
    out = render_ass([], CLEAN)
    assert "Dialogue:" not in out


def test_clean_and_pop_are_distinct_data_not_branches():
    assert CLEAN.name == "CLEAN"
    assert POP.name == "POP"
    assert CLEAN != POP
    assert CLEAN.highlight_colour != POP.highlight_colour


def test_render_ass_with_a_custom_preset_uses_its_style_name():
    custom = AssPreset(name="CUSTOM", font_name="Comic Sans MS", font_size=40)
    cards = [card([word("Hi", 0.0, 0.5)])]
    out = render_ass(cards, custom)

    assert "Style: CUSTOM,Comic Sans MS,40," in out
    assert "Dialogue: 0,0:00:00.00,0:00:00.50,CUSTOM,,0,0,0,," in out


def test_write_ass_writes_file(tmp_path):
    cards = [card([word("Hi", 0.0, 0.5)])]
    path = tmp_path / "styled.ass"

    write_ass(cards, path, POP)

    assert path.read_text(encoding="utf-8") == render_ass(cards, POP)
