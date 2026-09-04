"""Renders GLOW MINT through the real libass (ffmpeg's subtitles filter)
and checks the active word is a readable letterform on a halo, not a blob
(design 2026-09-04, section 3).

Skipped unless ASH_REAL_FFMPEG is set ("1", or the path of an ffmpeg.exe,
as in test_fontselect_real.py). From a worktree without bin\\ffmpeg.exe:

    $env:ASH_REAL_FFMPEG = "C:\\Users\\mbila\\Desktop\\ASH Captions\\bin\\ffmpeg.exe"
    .venv\\Scripts\\python.exe -m pytest tests/test_styles/test_glow_real.py -v

One one-word card is rendered onto a flat grey frame (no style colour is
grey) and the pixels around the word are classified: the style's active
colour (crisp fill), the style's outline colour (the letterform edge; it
did not exist in 0.4.2, where the outline was mint and blurred), the grey
background, and any remaining mint-tinted pixel (the blurred halo bleeding
into the grey).
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
from ash_captions.styles.library import list_styles, shipped_styles_dir
from ash_captions.styles.render import write_ass
from ash_captions.styles.schema import Style

_ENV = os.environ.get("ASH_REAL_FFMPEG", "")
pytestmark = pytest.mark.skipif(not _ENV, reason="set ASH_REAL_FFMPEG=1 to run against the real ffmpeg")

FILL = (0x4D, 0xFF, 0xC3)  # GLOW MINT colors.active
OUTLINE = (0x00, 0x33, 0x2A)  # GLOW MINT colors.outline
BACKGROUND = (0x80, 0x80, 0x80)  # the lavfi frame colour below
WORD_BOX = (340, 900, 740, 1020)  # around the one-word card centred at (540, 960) in a 1080x1920 frame


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
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour))


def _glow_mint() -> Style:
    return list_styles(user_dir=shipped_styles_dir().parent / "does-not-exist")["GLOW MINT"]


def _render_frame(style: Style, work: Path) -> Image.Image:
    fonts_dir = assets_fonts_dir()
    if not (fonts_dir / "Outfit-Regular.ttf").is_file():
        pytest.skip("Outfit-Regular.ttf not fetched (scripts/fetch_fonts.py)")
    card = Card(words=(Word(text="GLOW", start=0.0, end=2.0),), start=0.0, end=2.0)
    ass_path = work / f"{style.name}.ass"
    write_ass([card], ass_path, style, play_res=(1080, 1920))
    png = work / f"{style.name}.png"
    command = [
        str(_ffmpeg_path()), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=0x808080:s=1080x1920:r=25",
        "-vf", f"subtitles='{_escape(ass_path)}':fontsdir='{_escape(fonts_dir)}'",
        "-ss", "1", "-frames:v", "1", str(png),
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-2000:]
    return Image.open(png).convert("RGB")


def _classify(frame: Image.Image) -> dict[str, int]:
    counts = {"fill": 0, "outline": 0, "halo": 0, "background": 0, "other": 0}
    x0, y0, x1, y1 = WORD_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = frame.getpixel((x, y))
            if _near(pixel, FILL, 20):
                counts["fill"] += 1
            elif _near(pixel, OUTLINE, 20):
                counts["outline"] += 1
            elif _near(pixel, BACKGROUND, 4):
                counts["background"] += 1
            elif pixel[1] > pixel[0] + 30:  # green well above red: mint blended into the grey
                counts["halo"] += 1
            else:
                counts["other"] += 1
    return counts


@pytest.fixture(scope="module")
def glow_frame(tmp_path_factory) -> Image.Image:
    return _render_frame(_glow_mint(), tmp_path_factory.mktemp("glow_real"))


@pytest.fixture(scope="module")
def pop_frame(tmp_path_factory) -> Image.Image:
    """The same look with effect=pop: what the text layer alone should be."""
    definition = _glow_mint().to_dict()
    definition["name"] = "GLOW MINT AS POP"
    definition["active_word"] = {**definition["active_word"], "effect": "pop"}
    return _render_frame(Style.from_dict(definition, check_font=False), tmp_path_factory.mktemp("glow_pop"))


def test_active_word_is_crisp_fill_ringed_by_its_outline_on_a_halo(glow_frame):
    counts = _classify(glow_frame)
    assert counts["fill"] >= 1000, counts  # the word's own fill, unblurred
    assert counts["outline"] >= 1000, counts  # the style's dark outline: 0 in 0.4.2
    assert counts["halo"] >= 2000, counts  # mint bleeding into the grey around the word


def test_text_layer_matches_the_pop_rendering_of_the_same_look(glow_frame, pop_frame):
    glow, pop = _classify(glow_frame), _classify(pop_frame)
    assert pop["outline"] >= 1000, pop
    assert glow["outline"] >= 0.6 * pop["outline"], (glow, pop)  # the letterform edge survives under the halo
    assert glow["fill"] >= 0.6 * pop["fill"], (glow, pop)
    assert glow["halo"] > pop["halo"], (glow, pop)  # and there is a halo that pop does not have
