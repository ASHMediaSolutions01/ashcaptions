"""Pins today's ASS output for every non-glow active-word effect, byte for byte.

The v0.5 glow-halo change (design 2026-09-04, section 3) edits the shared
per-word event loop in render.py. These goldens were generated from the
pre-change renderer (commit 624dbd3) by ``write_goldens`` below, so a diff in
any non-glow style's output is a regression, not a re-baseline: fix the
renderer rather than regenerating the file, unless a spec deliberately
changes that effect's output.

Regenerate (only when a spec changes an effect on purpose), from the repo
root:

    .venv\\Scripts\\python.exe -m tests.test_styles.test_render_golden

Line endings: git's autocrlf may check the goldens out with CRLF, so both
sides are normalised to LF before comparing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.render import render_ass
from ash_captions.styles.schema import Style

GOLDEN_DIR = Path(__file__).parent / "golden"
PLAY_RES = (1080, 1920)

# One fixture style per non-glow effect. Entrance/exit/letter-spacing/case
# are varied so the leading override block (\fad, \move, the merged-fad
# path on a one-word card), \fsp and str.upper() are all pinned as well.
FIXTURE_STYLES: dict[str, dict] = {
    "pop": {
        "name": "FIX POP", "size": 80, "letter_spacing": 0.5,
        "active_word": {"effect": "pop", "scale": 1.12},
        "entrance": {"effect": "rise", "duration_ms": 140}, "exit": {"effect": "none", "duration_ms": 0},
    },
    "none": {
        "name": "FIX NONE", "active_word": {"effect": "none"},
        "entrance": {"effect": "fade", "duration_ms": 300}, "exit": {"effect": "fade", "duration_ms": 300},
    },
    "shake": {
        "name": "FIX SHAKE", "uppercase": True, "active_word": {"effect": "shake"},
        "entrance": {"effect": "slide", "duration_ms": 160}, "exit": {"effect": "slide", "duration_ms": 160},
    },
    "karaoke": {
        "name": "FIX KARAOKE", "active_word": {"effect": "karaoke"},
        "entrance": {"effect": "fade", "duration_ms": 200}, "exit": {"effect": "fade", "duration_ms": 200},
    },
    "box": {
        "name": "FIX BOX", "active_word": {"effect": "box", "box": True},
        "layout": {"position": "bottom", "margin_v": 160},
    },
    "scale_box": {
        "name": "FIX SCALE BOX", "active_word": {"effect": "scale_box", "scale": 1.3, "box": True},
        "entrance": {"effect": "rise", "duration_ms": 140},
    },
    "card_box": {
        "name": "FIX CARD BOX", "active_word": {"effect": "card_box"},
        "layout": {"position": "top", "align": "left"},
    },
}


def _word(text: str, start: float, end: float) -> Word:
    return Word(text=text, start=start, end=end)


def _card(words: list[Word]) -> Card:
    return Card(words=tuple(words), start=words[0].start, end=words[-1].end)


def golden_cards() -> list[Card]:
    return [
        _card([_word("hello", 0.0, 0.3), _word("there", 0.3, 0.6), _word("world", 0.6, 1.0)]),
        _card([_word("one-word", 1.2, 1.5)]),
        _card([_word("use", 2.0, 2.3), _word("{name}", 2.3, 2.6), _word("a\\b", 2.6, 3.0)]),
    ]


def _render(effect: str) -> bytes:
    style = Style.from_dict(FIXTURE_STYLES[effect], check_font=False)
    return render_ass(golden_cards(), style, play_res=PLAY_RES).encode("utf-8")


@pytest.mark.parametrize("effect", sorted(FIXTURE_STYLES))
def test_non_glow_output_is_byte_identical_to_golden(effect):
    expected = (GOLDEN_DIR / f"{effect}.ass").read_bytes().replace(b"\r\n", b"\n")
    assert _render(effect).replace(b"\r\n", b"\n") == expected


def test_non_glow_styles_only_use_layer_0():
    for effect in FIXTURE_STYLES:
        for line in _render(effect).decode("utf-8").splitlines():
            if line.startswith("Dialogue:"):
                assert line.startswith("Dialogue: 0,"), (effect, line)


def write_goldens() -> None:
    GOLDEN_DIR.mkdir(exist_ok=True)
    for effect in FIXTURE_STYLES:
        (GOLDEN_DIR / f"{effect}.ass").write_bytes(_render(effect))
        print("wrote", GOLDEN_DIR / f"{effect}.ass")


if __name__ == "__main__":
    write_goldens()
