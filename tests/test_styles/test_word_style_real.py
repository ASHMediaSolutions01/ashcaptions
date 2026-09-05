"""Burns a per-word colour override through the real libass and checks the
pixels (v0.6 design, section 2 -- "a real-libass pixel test that a word
given #FFD166 is actually drawn in that colour").

Skipped unless ASH_REAL_FFMPEG is set ("1", or the path of an ffmpeg.exe,
as in test_glow_real.py). From a worktree without bin\\ffmpeg.exe:

    $env:ASH_REAL_FFMPEG = "C:\\Users\\mbila\\Desktop\\ASH Captions\\bin\\ffmpeg.exe"
    .venv\\Scripts\\python.exe -m pytest tests/test_styles/test_word_style_real.py -v

One two-word card is rendered onto a flat grey frame in a look whose text
*and* active colour are white, so the only thing that can put a non-white,
non-grey pixel on the frame is the override. The frame is then split down
the middle: the first word must be white with no amber anywhere near it,
the second amber. A second render with the same card and no overrides
proves the amber is the override's doing and not the look's.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.fonts import assets_fonts_dir
from ash_captions.styles.render import write_ass
from ash_captions.styles.schema import Style, WordStyle

_ENV = os.environ.get("ASH_REAL_FFMPEG", "")
pytestmark = pytest.mark.skipif(not _ENV, reason="set ASH_REAL_FFMPEG=1 to run against the real ffmpeg")

WHITE = (0xFF, 0xFF, 0xFF)
AMBER = (0xFF, 0xD1, 0x66)
BACKGROUND = (0x80, 0x80, 0x80)  # the lavfi frame colour below
# The card is centred in a 1080x1920 frame; the gap between the two words
# straddles x=540, so neither band can contain a pixel of the other word.
LEFT_BAND = (40, 900, 500, 1020)
RIGHT_BAND = (580, 900, 1040, 1020)

WORDS = (Word(text="AAAA", start=0.0, end=2.0), Word(text="BBBB", start=2.0, end=4.0))
CARD = Card(words=WORDS, start=0.0, end=4.0)
SECOND_WORD = (2.0, 4.0)


def _ffmpeg_path() -> Path:
    if _ENV not in ("", "1", "true") and Path(_ENV).is_file():
        return Path(_ENV)
    from ash_captions.config import find_binary

    found = find_binary("ffmpeg")
    if found is None:
        pytest.skip("no ffmpeg.exe found (bin/ffmpeg.exe or PATH)")
    return found


def _escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _near(pixel: tuple[int, int, int], colour: tuple[int, int, int], tolerance: int) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour, strict=True))


def _plain_look() -> Style:
    # Text and active colour both white: any colour on the frame other than
    # white, black (the outline) or grey came from the override.
    return Style.from_dict(
        {
            "name": "PLAIN WHITE",
            "font": "Inter",
            "size": 90,
            "active_word": {"effect": "none"},
            "entrance": {"effect": "none", "duration_ms": 0},
            "exit": {"effect": "none", "duration_ms": 0},
            "colors": {"text": "#FFFFFF", "active": "#FFFFFF", "outline": "#000000"},
            "layout": {"position": "center", "max_words": 4},
        },
        check_font=False,
    )


def _render_frame(word_styles, work: Path, name: str) -> Image.Image:
    fonts_dir = assets_fonts_dir()
    if not (fonts_dir / "Inter-Regular.ttf").is_file():
        pytest.skip("Inter-Regular.ttf not fetched (python -m ash_captions.styles.fonts download)")
    ass_path = work / f"{name}.ass"
    write_ass([CARD], ass_path, _plain_look(), play_res=(1080, 1920), word_styles=word_styles)
    png = work / f"{name}.png"
    command = [
        str(_ffmpeg_path()), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=0x808080:s=1080x1920:r=25",
        "-vf", f"subtitles='{_escape(ass_path)}':fontsdir='{_escape(fonts_dir)}'",
        "-ss", "1", "-frames:v", "1", str(png),
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-2000:]
    return Image.open(png).convert("RGB")


def _count(frame: Image.Image, band: tuple[int, int, int, int]) -> dict[str, int]:
    counts = {"white": 0, "amber": 0, "background": 0, "other": 0}
    x0, y0, x1, y1 = band
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = frame.getpixel((x, y))
            if _near(pixel, WHITE, 20):
                counts["white"] += 1
            elif _near(pixel, AMBER, 24):
                counts["amber"] += 1
            elif _near(pixel, BACKGROUND, 4):
                counts["background"] += 1
            else:
                counts["other"] += 1
    return counts


@pytest.fixture(scope="module")
def styled_frame(tmp_path_factory) -> Image.Image:
    return _render_frame(
        {SECOND_WORD: WordStyle(colour="#FFD166")}, tmp_path_factory.mktemp("word_style_real"), "styled"
    )


@pytest.fixture(scope="module")
def plain_frame(tmp_path_factory) -> Image.Image:
    return _render_frame(None, tmp_path_factory.mktemp("word_style_plain"), "plain")


def test_the_styled_word_is_drawn_in_its_own_colour(styled_frame):
    right = _count(styled_frame, RIGHT_BAND)
    assert right["amber"] >= 1000, right


def test_its_neighbour_is_untouched(styled_frame):
    left = _count(styled_frame, LEFT_BAND)
    assert left["white"] >= 1000, left
    assert left["amber"] == 0, left


def test_the_same_card_without_the_override_has_no_amber_anywhere(plain_frame):
    for band in (LEFT_BAND, RIGHT_BAND):
        counts = _count(plain_frame, band)
        assert counts["white"] >= 1000, (band, counts)
        assert counts["amber"] == 0, (band, counts)
