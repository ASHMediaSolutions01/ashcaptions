"""Tests for ash_captions.styles.render -- the animated ASS renderer.

Three things matter most here (per the brief): the text escaping is a
security boundary as well as a rendering concern, the validation lives in
schema.py (tested there) but must still guarantee render_ass never
crashes on a shipped style, and the renderer must not special-case any
style by name -- everything must come from its data fields.
"""
from __future__ import annotations

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.library import list_styles, shipped_styles_dir
from ash_captions.styles.render import render_ass, write_ass
from ash_captions.styles.schema import Style


def word(text, start, end):
    return Word(text=text, start=start, end=end)


def card(words):
    return Card(words=tuple(words), start=words[0].start, end=words[-1].end)


def sample_cards():
    return [
        card([word("hello", 0.0, 0.3), word("there", 0.3, 0.6), word("world", 0.6, 1.0)]),
        card([word("second", 1.2, 1.5), word("card", 1.5, 1.9)]),
    ]


# ---------------------------------------------------------------------------
# structural basics
# ---------------------------------------------------------------------------


def test_render_ass_includes_script_info_and_style_sections():
    style = Style.from_dict({"name": "BASIC"}, check_font=False)
    out = render_ass(sample_cards(), style)

    assert "[Script Info]" in out
    assert "[V4+ Styles]" in out
    assert "[Events]" in out
    assert "PlayResX: 1080" in out
    assert "PlayResY: 1920" in out
    assert "Style: BASIC," in out


def test_render_ass_respects_custom_play_res():
    style = Style.from_dict({"name": "BASIC"}, check_font=False)
    out = render_ass(sample_cards(), style, play_res=(1920, 1080))

    assert "PlayResX: 1920" in out
    assert "PlayResY: 1080" in out


def test_render_ass_empty_cards_has_no_dialogue_lines():
    style = Style.from_dict({"name": "BASIC"}, check_font=False)
    out = render_ass([], style)
    assert "Dialogue:" not in out


def test_write_ass_writes_file(tmp_path):
    style = Style.from_dict({"name": "BASIC"}, check_font=False)
    path = tmp_path / "styled.ass"

    write_ass(sample_cards(), path, style)

    assert path.read_text(encoding="utf-8") == render_ass(sample_cards(), style)


# ---------------------------------------------------------------------------
# escaping -- a literal {, } or \ must never become an override tag
# ---------------------------------------------------------------------------


def test_literal_braces_are_escaped_not_left_as_override_syntax():
    style = Style.from_dict({"name": "BASIC", "active_word": {"effect": "none"}}, check_font=False)
    cards = [card([word("use", 0.0, 0.3), word("{evil}", 0.3, 0.6)])]

    out = render_ass(cards, style)

    # The literal braces from the transcript must not survive as raw
    # ASCII '{' / '}' inside the Text field -- only our own deliberately
    # constructed override blocks may use those characters.
    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    for line in dialogue_lines:
        text_field = line.split(",", 9)[-1]
        # Every '{' in the text field must be immediately followed later
        # by a matching '}' that we generated ourselves for a real tag --
        # simplest robust check: the original literal token must be gone.
        assert "{evil}" not in text_field
    assert "｛evil｝" in out


def test_literal_backslash_is_escaped():
    style = Style.from_dict({"name": "BASIC"}, check_font=False)
    cards = [card([word("path", 0.0, 0.3), word("a\\b", 0.3, 0.6)])]

    out = render_ass(cards, style)

    assert "a\\b" not in out.split("[Events]")[1]
    assert "a＼b" in out


def test_escaped_braces_still_leave_override_blocks_balanced():
    # A cheap sanity check that our own tags are still well-formed even
    # when the transcript is adversarial: every event's Text field must
    # have matched, non-nested {..} blocks made only of our own tags.
    style = Style.from_dict(
        {"name": "BASIC", "active_word": {"effect": "pop"}}, check_font=False
    )
    cards = [card([word("{{{", 0.0, 0.3), word("}}}\\\\", 0.3, 0.6)])]

    out = render_ass(cards, style)

    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    for line in dialogue_lines:
        text_field = line.split(",", 9)[-1]
        assert text_field.count("{") == text_field.count("}")


# ---------------------------------------------------------------------------
# effect -> tag mapping (spec 7A.1)
# ---------------------------------------------------------------------------


def test_pop_effect_emits_fscx_fscy_transform_back_to_100():
    style = Style.from_dict({"name": "X", "active_word": {"effect": "pop", "scale": 1.2}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    assert "\\fscx120\\fscy120" in out
    assert "\\fscx100\\fscy100" in out


def test_karaoke_effect_emits_kf_and_one_event_per_card():
    style = Style.from_dict({"name": "X", "active_word": {"effect": "karaoke"}}, check_font=False)
    cards = sample_cards()
    out = render_ass(cards, style)

    dialogue_lines = [line for line in out.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue_lines) == len(cards)
    assert "\\kf" in out


def test_box_effect_uses_borderstyle_3_companion_style():
    style = Style.from_dict(
        {"name": "X", "active_word": {"effect": "box", "box": True}}, check_font=False
    )
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    header = out.split("[Events]")[0]
    assert "X_BOX" in header
    box_style_line = next(line for line in header.splitlines() if line.startswith("Style: X_BOX,"))
    fields = box_style_line.split(",")
    # BorderStyle is column index 15 in the Format line this module writes.
    assert fields[15] == "3"


def test_scale_box_effect_combines_box_style_and_scale_transform():
    style = Style.from_dict(
        {"name": "X", "active_word": {"effect": "scale_box", "scale": 1.3, "box": True}},
        check_font=False,
    )
    out = render_ass([card([word("one", 0.0, 0.3)])], style)

    assert "X_BOX" in out
    assert "\\fscx130\\fscy130" in out


def test_shake_effect_emits_frz_transform_chain():
    style = Style.from_dict({"name": "X", "active_word": {"effect": "shake"}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    assert out.count("\\frz") >= 4


def test_glow_effect_emits_blur():
    style = Style.from_dict({"name": "X", "active_word": {"effect": "glow"}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    assert "\\blur" in out


def test_entrance_fade_emits_fad_tag():
    style = Style.from_dict({"name": "X", "entrance": {"effect": "fade", "duration_ms": 200}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    assert "\\fad(200," in out


def test_entrance_rise_emits_move_tag():
    style = Style.from_dict({"name": "X", "entrance": {"effect": "rise", "duration_ms": 150}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    assert "\\move(" in out


def test_entrance_slide_emits_move_tag():
    style = Style.from_dict({"name": "X", "entrance": {"effect": "slide", "duration_ms": 150}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3), word("two", 0.3, 0.6)])], style)

    assert "\\move(" in out


def test_letter_spacing_emits_fsp_tag():
    style = Style.from_dict({"name": "X", "letter_spacing": 2.5}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3)])], style)

    assert "\\fsp2.50" in out


def test_zero_letter_spacing_omits_fsp_tag():
    style = Style.from_dict({"name": "X", "letter_spacing": 0}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3)])], style)

    assert "\\fsp" not in out


def test_uppercase_transform_applied_to_every_word():
    style = Style.from_dict({"name": "X", "uppercase": True}, check_font=False)
    out = render_ass([card([word("hello", 0.0, 0.3), word("there", 0.3, 0.6)])], style)

    events_section = out.split("[Events]")[1]
    assert "HELLO" in events_section
    assert "THERE" in events_section
    assert "hello" not in events_section
    assert "there" not in events_section


def test_position_bottom_uses_alignment_2():
    style = Style.from_dict({"name": "X", "layout": {"position": "bottom"}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3)])], style)
    style_line = next(l for l in out.splitlines() if l.startswith("Style: X,"))
    assert style_line.split(",")[18] == "2"


def test_position_top_uses_alignment_8():
    style = Style.from_dict({"name": "X", "layout": {"position": "top"}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3)])], style)
    style_line = next(l for l in out.splitlines() if l.startswith("Style: X,"))
    assert style_line.split(",")[18] == "8"


def test_position_center_uses_alignment_5():
    style = Style.from_dict({"name": "X", "layout": {"position": "center"}}, check_font=False)
    out = render_ass([card([word("one", 0.0, 0.3)])], style)
    style_line = next(l for l in out.splitlines() if l.startswith("Style: X,"))
    assert style_line.split(",")[18] == "5"


def test_layout_margins_appear_in_style_line():
    style = Style.from_dict(
        {"name": "X", "layout": {"margin_l": 11, "margin_r": 22, "margin_v": 33}}, check_font=False
    )
    out = render_ass([card([word("one", 0.0, 0.3)])], style)
    style_line = next(l for l in out.splitlines() if l.startswith("Style: X,"))
    fields = style_line.split(",")
    assert fields[19:22] == ["11", "22", "33"]


# ---------------------------------------------------------------------------
# "styles are data, never a branch" (spec 7A.2)
# ---------------------------------------------------------------------------


def test_every_shipped_style_renders_without_error():
    cards = sample_cards()
    for name, style in list_styles(user_dir=shipped_styles_dir().parent / "does-not-exist").items():
        out = render_ass(cards, style)
        assert "Dialogue:" in out, f"{name} produced no dialogue"


def test_renderer_output_is_driven_by_fields_not_by_name():
    """Two styles differing *only* in name must render identically once
    the name substitution is undone -- proving the renderer never reads
    ``style.name`` to decide behaviour, only to label the ASS Style."""
    a = Style.from_dict({"name": "ALPHA", "active_word": {"effect": "shake"}}, check_font=False)
    b = Style.from_dict({"name": "BETA", "active_word": {"effect": "shake"}}, check_font=False)

    out_a = render_ass(sample_cards(), a)
    out_b = render_ass(sample_cards(), b)

    assert out_a.replace("ALPHA", "BETA") == out_b


def test_same_name_different_effects_render_differently():
    """The inverse check: identical name, different effect data, must
    change the output -- confirming behaviour tracks the effect field,
    not a name-keyed lookup table hiding behind the enum."""
    pop = Style.from_dict({"name": "SAME", "active_word": {"effect": "pop"}}, check_font=False)
    shake = Style.from_dict({"name": "SAME", "active_word": {"effect": "shake"}}, check_font=False)

    out_pop = render_ass(sample_cards(), pop)
    out_shake = render_ass(sample_cards(), shake)

    assert out_pop != out_shake
