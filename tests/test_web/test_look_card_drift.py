"""The look cards and the burn must agree on every tag formula.

``web/static/look_card_ass.js`` is a hand-kept JavaScript port of
``styles/render.py``, ``styles/ass_format.py`` and ``styles/render_word.py``:
Python cannot run in a browser, and a server round trip per card -- 36 of
them, re-filtered on every keypress -- is what the spec ruled out. So the
Styles page animates its own ``.ass``.

The existing ``js/look_card_ass.test.js`` checks that file against tag
strings a person typed into it. That catches a mistake in the JavaScript
and nothing else: change a formula in ``render.py`` and it keeps passing,
while every look card on the Styles page starts previewing something the
burn will not produce. Nobody would notice until a client did.

This runs the *same inputs* through both and demands the same output. If
it fails, one of the two moved and the JavaScript is the one to fix --
Python is the source of truth for what a real job renders.

The one thing deliberately not compared is the frame: the cards use a
1080x240 PlayRes and force centred placement, because drawing a 1080x1920
frame into a 40px strip left half the library blank. That is a decision
about the card, not a formula, so it lives in ``look_card_ass.js`` alone.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ash_captions.styles import ass_format, render
from ash_captions.styles.render_word import active_word_tags
from ash_captions.styles.schema import Style

DRIVER = Path(__file__).parent / "js" / "look_card_drift.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

BASE: dict = {
    "name": "TEST LOOK",
    "font": "Inter",
    "size": 72,
    "uppercase": False,
    "letter_spacing": 0,
    "colors": {"text": "#FFFFFF", "active": "#FFD166", "outline": "#000000",
               "shadow": "#00000090", "box": "#00000000"},
    "active_word": {"effect": "pop", "scale": 1.12, "box": False},
    "entrance": {"effect": "fade", "duration_ms": 120},
    "exit": {"effect": "none", "duration_ms": 0},
    "layout": {"position": "bottom", "max_words": 4, "margin_l": 80, "margin_r": 80,
               "margin_v": 120, "align": "center"},
}


def look(**overrides) -> dict:
    merged = json.loads(json.dumps(BASE))
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def ask_node(cases: list[dict]) -> list:
    result = subprocess.run(
        ["node", str(DRIVER)],
        input=json.dumps(cases), capture_output=True, text=True,
        # Windows would otherwise decode Node's stdout as cp1252 and turn a
        # style name with an umlaut into a false drift.
        encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def compare(cases: list[dict], expected: list) -> None:
    """One Node call for the whole matrix, then a per-case assert naming
    the input that drifted."""
    actual = ask_node(cases)
    wrong = [
        (case, want, got)
        for case, want, got in zip(cases, expected, actual, strict=True)
        if want != got
    ]
    assert not wrong, "\n".join(
        f"{c['fn']}{json.dumps({k: v for k, v in c.items() if k != 'fn'})[:160]}\n"
        f"    python: {w!r}\n    js:     {g!r}"
        for c, w, g in wrong
    )


# ---------------------------------------------------------------------------
# ass_format.py
# ---------------------------------------------------------------------------

COLOURS = ["#FFFFFF", "#000000", "#FFD166", "#00E28A", "#00000090", "#123456AB", "#FFFFFF00"]


def test_colours_convert_the_same_way():
    cases = [{"fn": "assStyleColour", "colour": c} for c in COLOURS]
    cases += [{"fn": "assInlineColour", "colour": c} for c in COLOURS]
    expected = [ass_format.ass_style_colour(c) for c in COLOURS]
    expected += [ass_format.ass_inline_colour(c) for c in COLOURS]
    compare(cases, expected)


def test_timestamps_format_the_same_way():
    times = [0, 0.004, 0.005, 1.0, 61.23, 59.999, 3600, 3661.789, 12.345]
    cases = [{"fn": "formatAssTime", "seconds": t} for t in times]
    compare(cases, [ass_format.format_ass_time(t) for t in times])


def test_every_position_and_alignment_maps_to_the_same_numpad_value():
    """All nine. A disagreement here puts a card's caption in a different
    corner from the burn, which is the kind of thing that looks like a
    taste difference rather than a bug."""
    pairs = [(p, a) for p in ("bottom", "center", "top", "lower_third")
             for a in ("left", "center", "right")]
    cases = [{"fn": "assAlignment", "position": p, "align": a} for p, a in pairs]
    compare(cases, [ass_format.ass_alignment(p, a) for p, a in pairs])


def test_style_names_are_sanitised_the_same_way():
    names = ["CLEAN", "REEL ESTATE", "a,b", "with\nnewline", "Ünïcøde", "", "  padded  ", "x" * 80]
    cases = [{"fn": "safeStyleName", "name": n} for n in names]
    compare(cases, [ass_format.safe_style_name(n) for n in names])


# ---------------------------------------------------------------------------
# render.py's motion tags
# ---------------------------------------------------------------------------

MOTION_LOOKS = [
    look(entrance={"effect": e, "duration_ms": d}, exit={"effect": x, "duration_ms": d})
    for e in ("none", "fade", "rise", "slide")
    for x in ("none", "fade", "rise", "slide")
    for d in (0, 60, 120, 400)
]
POINTS = [(540.0, 1800.0), (0.0, 0.0), (123.5, 47.25)]
EVENTS = [40, 120, 500, 2000]


def test_entrance_tags_agree():
    cases, expected = [], []
    for definition in MOTION_LOOKS:
        style = Style.from_dict(definition, check_font=False)
        for x, y in POINTS:
            for event_ms in EVENTS:
                cases.append({"fn": "entranceTag", "style": definition,
                              "x": x, "y": y, "event_ms": event_ms})
                expected.append(render._entrance_tag(style, x, y, event_ms))
    compare(cases, expected)


def test_exit_tags_agree():
    cases, expected = [], []
    for definition in MOTION_LOOKS:
        style = Style.from_dict(definition, check_font=False)
        for x, y in POINTS:
            for event_ms in EVENTS:
                cases.append({"fn": "exitTag", "style": definition,
                              "x": x, "y": y, "event_ms": event_ms})
                expected.append(render._exit_tag(style, x, y, event_ms))
    compare(cases, expected)


def test_the_whole_leading_override_agrees():
    """The one that matters most: entrance, exit, letter spacing and the
    positioning tag assembled in one string, in one order."""
    looks = MOTION_LOOKS[::5] + [
        look(letter_spacing=2.5), look(letter_spacing=-1),
        look(layout={"position": "top", "align": "left"}),
        look(layout={"position": "center", "align": "right"}),
        look(layout={"position": "lower_third"}),
    ]
    cases, expected = [], []
    for definition in looks:
        style = Style.from_dict(definition, check_font=False)
        for x, y in POINTS:
            for is_first, is_last in ((True, False), (False, True), (True, True), (False, False)):
                cases.append({"fn": "leadingOverride", "style": definition, "x": x, "y": y,
                              "is_first": is_first, "is_last": is_last, "event_ms": 400})
                expected.append(render._leading_override(
                    style, x, y, is_first=is_first, is_last=is_last, event_ms=400))
    compare(cases, expected)


# ---------------------------------------------------------------------------
# render_word.py's active-word tags
# ---------------------------------------------------------------------------


def test_active_word_tags_agree_for_every_effect():
    looks = [
        look(active_word={"effect": effect, "scale": scale, "box": box})
        for effect in ("none", "pop", "box", "scale_box", "card_box", "karaoke", "shake", "glow")
        for scale in (1.0, 1.12, 1.5)
        for box in (False, True)
    ]
    cases, expected = [], []
    for definition in looks:
        style = Style.from_dict(definition, check_font=False)
        active, text = definition["colors"]["active"], definition["colors"]["text"]
        cases.append({"fn": "activeWordTags", "style": definition,
                      "active_colour": active, "text_colour": text})
        # The JS returns [on, off]; Python returns a tuple of the same two.
        expected.append(list(active_word_tags(style, active, text)))
    compare(cases, expected)
