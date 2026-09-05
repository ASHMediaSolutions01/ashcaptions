"""render_ass(anchor=...) pins every event to one point (v0.5 spec §1):
\\pos for static events, a \\move that starts or ends there for rise and
slide; without an anchor the output is byte-identical to before. The
fractions-to-pixels conversion (anchor_pixels) is tested here too."""
from __future__ import annotations

import re

import pytest

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.library import list_styles, shipped_styles_dir
from ash_captions.styles.render import DEFAULT_PLAY_RES, anchor_pixels, render_ass, write_ass
from ash_captions.styles.schema import Style

ANCHOR = (540.0, 480.0)  # caption_x=0.5, caption_y=0.25 of a 1080x1920 frame
_POS = re.compile(r"\\pos\(([-\d.]+),([-\d.]+)\)")
_MOVE = re.compile(r"\\move\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)")


def word(text, start, end):
    return Word(text=text, start=start, end=end)


def card(words):
    return Card(words=tuple(words), start=words[0].start, end=words[-1].end)


def cards():
    return [
        card([word("hello", 0.0, 0.3), word("there", 0.3, 0.6), word("world", 0.6, 1.0)]),
        card([word("one", 1.2, 1.9)]),
    ]


def dialogue_lines(out):
    return [line for line in out.splitlines() if line.startswith("Dialogue:")]


def pinned_to(line, anchor):
    """True when the event sits at `anchor`: a \\pos there, or a \\move that
    starts or ends there (an exit motion leaves the anchor by design)."""
    ax, ay = anchor
    pos = _POS.search(line)
    if pos:
        return (float(pos.group(1)), float(pos.group(2))) == (ax, ay)
    move = _MOVE.search(line)
    if not move:
        return False
    x1, y1, x2, y2 = (float(g) for g in move.groups())
    return (x1, y1) == (ax, ay) or (x2, y2) == (ax, ay)


STYLES = {
    "none": {"active_word": {"effect": "none"}},
    "pop+fade": {"active_word": {"effect": "pop"}, "entrance": {"effect": "fade", "duration_ms": 120}},
    "rise in and out": {"entrance": {"effect": "rise", "duration_ms": 90}, "exit": {"effect": "rise", "duration_ms": 90}},
    "slide in": {"entrance": {"effect": "slide", "duration_ms": 150}},
    "fade in and out": {"entrance": {"effect": "fade", "duration_ms": 100}, "exit": {"effect": "fade", "duration_ms": 100}},
    "karaoke": {"active_word": {"effect": "karaoke"}},
    "box": {"active_word": {"effect": "box", "box": True}},
    "scale_box": {"active_word": {"effect": "scale_box", "box": True}},
    "top left": {"layout": {"position": "top", "align": "left"}},
}


@pytest.mark.parametrize("extra", list(STYLES.values()), ids=list(STYLES))
def test_every_event_is_pinned_to_the_anchor(extra):
    style = Style.from_dict({"name": "X", **extra}, check_font=False)
    out = render_ass(cards(), style, play_res=(1080, 1920), anchor=ANCHOR)
    lines = dialogue_lines(out)
    assert lines
    for line in lines:
        assert pinned_to(line, ANCHOR), line
        assert not (_POS.search(line) and _MOVE.search(line)), f"\\pos and \\move on one line: {line}"


def test_every_shipped_style_is_pinned_when_asked():
    for name, style in list_styles(user_dir=shipped_styles_dir().parent / "does-not-exist").items():
        out = render_ass(cards(), style, play_res=(1920, 1080), anchor=(960.0, 270.0))
        for line in dialogue_lines(out):
            assert pinned_to(line, (960.0, 270.0)), (name, line)


def test_the_style_lines_alignment_is_unchanged_by_an_anchor():
    style = Style.from_dict({"name": "X", "layout": {"position": "top", "align": "left"}}, check_font=False)
    plain = render_ass(cards(), style)
    pinned = render_ass(cards(), style, anchor=ANCHOR)
    style_line = lambda out: next(line for line in out.splitlines() if line.startswith("Style: X,"))  # noqa: E731
    assert style_line(plain) == style_line(pinned)
    assert style_line(pinned).split(",")[18] == "7"  # top-left stays \an7 around the moved anchor


def test_without_an_anchor_output_is_byte_identical_to_today():
    style = Style.from_dict(
        {
            "name": "X",
            "active_word": {"effect": "none"},
            "entrance": {"effect": "none"},
            "exit": {"effect": "none"},
            "colors": {"text": "#FFFFFF", "active": "#FF2E63"},
        },
        check_font=False,
    )
    one = [card([word("hello", 0.0, 0.3), word("there", 0.3, 0.6)])]
    out = render_ass(one, style, play_res=(1080, 1920))
    assert out == render_ass(one, style, play_res=(1080, 1920), anchor=None)
    # Today's exact events for this style (captured before the anchor work).
    assert dialogue_lines(out) == [
        "Dialogue: 0,0:00:00.00,0:00:00.30,X,,0,0,0,,{\\c&H632EFF&}hello{\\c&HFFFFFF&} {\\c&HFFFFFF&}there",
        "Dialogue: 0,0:00:00.30,0:00:00.60,X,,0,0,0,,{\\c&HFFFFFF&}hello {\\c&H632EFF&}there{\\c&HFFFFFF&}",
    ]
    for name, shipped in list_styles(user_dir=shipped_styles_dir().parent / "does-not-exist").items():
        assert "\\pos(" not in render_ass(cards(), shipped), name


def test_write_ass_takes_the_anchor(tmp_path):
    style = Style.from_dict({"name": "X"}, check_font=False)
    path = write_ass(cards(), tmp_path / "pinned.ass", style, play_res=(1080, 1920), anchor=ANCHOR)
    assert path.read_text(encoding="utf-8") == render_ass(cards(), style, play_res=(1080, 1920), anchor=ANCHOR)


@pytest.mark.parametrize("bad", [("a", 1.0), (1.0,), (1.0, 2.0, 3.0), (float("nan"), 1.0), (float("inf"), 1.0), 42])
def test_a_malformed_anchor_is_refused(bad):
    style = Style.from_dict({"name": "X"}, check_font=False)
    with pytest.raises(ValueError):
        render_ass(cards(), style, anchor=bad)


# -- fractions -> PlayRes pixels ---------------------------------------------


def test_anchor_pixels_portrait_and_landscape():
    assert anchor_pixels((0.5, 0.25), (1080, 1920)) == (540.0, 480.0)
    assert anchor_pixels((0.5, 0.25), (1920, 1080)) == (960.0, 270.0)
    assert anchor_pixels((0.0, 1.0), (1920, 1080)) == (0.0, 1080.0)


def test_anchor_pixels_none_passes_through_and_defaults_the_play_res():
    assert anchor_pixels(None, (1920, 1080)) is None
    assert anchor_pixels((0.5, 0.5), None) == (DEFAULT_PLAY_RES[0] / 2, DEFAULT_PLAY_RES[1] / 2)


@pytest.mark.parametrize("bad", [(1.5, 0.5), (0.5, -0.1), ("x", 0.5), (0.5,), (0.1, 0.2, 0.3)])
def test_anchor_pixels_rejects_fractions_outside_the_frame(bad):
    with pytest.raises(ValueError):
        anchor_pixels(bad, (1080, 1920))
