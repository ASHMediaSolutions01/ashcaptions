"""Burns free-placement captions with the real libass (ffmpeg's
subtitles filter) and measures the pixels (design 2026-09-05, section 5).

A look that passes its unit tests and looks wrong on screen is the
failure mode this project has had over and over, so the four claims the
reel look rests on are checked on burned frames rather than on strings:

  1. Four treatments live inside one caption -- four words, four sizes,
     four colours, four places, one card.
  2. The words *accumulate*: each one stays while the next arrives.
  3. The entrances animate -- a stretch-collapsing word is measurably
     wider 40 ms in than when it settles, at the same height.
  4. The colour and the lean land: the active-role word is drawn in the
     look's active colour, and an italic slot really slants.

Skipped unless ASH_REAL_FFMPEG is set ("1", or the path of an ffmpeg.exe,
as in test_glow_real.py). From a worktree without bin\\ffmpeg.exe:

    $env:ASH_REAL_FFMPEG = "C:\\Users\\mbila\\Desktop\\ASH Captions\\bin\\ffmpeg.exe"
    .venv\\Scripts\\python.exe -m pytest tests/test_styles/test_free_real.py -v

The four-treatment fixture uses four *separable* colours (white, yellow,
magenta, cyan) so each word's glyphs can be masked out of the frame
independently -- the same reason test_render_golden.py renders fixture
styles rather than shipped ones. The shipped REEL ESTATE, whose third
treatment is deliberately the same near-black as every word's outline, is
burned in its own test.
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

PLAY_RES = (1080, 1920)
BACKGROUND = (0x80, 0x80, 0x80)  # the lavfi frame colour: no look's colour is grey

# Four treatments, three colours -- which is the reference's own
# structure: its two small italic words are both white. Two words sharing
# a colour are still separable, because each is measured inside a window
# around its own slot.
#
# `colors.shadow` is left transparent on purpose: setting it to anything
# else gives *every* word a drop shadow (ass_format.ass_header turns the
# Shadow column on the moment it is not #00000000), which lands that
# colour all over the frame and makes nothing measurable. That is why
# "shadow" is a legal slot role but a poor one.
#
# The four words share a vertical profile -- an ascender, no descender --
# so their glyph heights are comparable across slots.
FIXTURE = {
    "name": "FREE REAL",
    "font": "Anton",
    "size": 160,
    "colors": {
        "text": "#FFFFFF", "active": "#FFD400", "outline": "#101010",
        "shadow": "#00000000", "box": "#00E5FF",
    },
    "active_word": {"effect": "none"},
    "entrance": {"effect": "none", "duration_ms": 0},
    "exit": {"effect": "none", "duration_ms": 0},
    "layout": {
        "mode": "free", "position": "center", "max_words": 4,
        "slots": [
            {"x": 0.28, "y": 0.22, "scale": 0.55, "role": "text", "italic": True,
             "font": "Poppins", "entrance": "fade_settle"},
            {"x": 0.50, "y": 0.44, "scale": 2.30, "role": "active", "entrance": "stretch_collapse"},
            {"x": 0.40, "y": 0.64, "scale": 1.15, "role": "box", "entrance": "stretch_collapse"},
            {"x": 0.70, "y": 0.82, "scale": 0.75, "role": "text", "entrance": "none"},
        ],
    },
}
# The card below is four words long and none of them is a connector, so
# assign_slots hands them out biggest slot first in spoken order.
# slot index -> (word, the RGB its role resolves to)
SLOT_WORD = {1: ("hill", (0xFF, 0xD4, 0x00)), 2: ("bolt", (0x00, 0xE5, 0xFF)),
             3: ("kite", (0xFF, 0xFF, 0xFF)), 0: ("dial", (0xFF, 0xFF, 0xFF))}

WORD_STARTS = {"hill": 0.0, "bolt": 0.5, "kite": 1.0, "dial": 1.5}
CARD_END = 3.0


def _ffmpeg_path() -> Path:
    if _ENV not in ("", "1", "true") and Path(_ENV).is_file():
        return Path(_ENV)
    from ash_captions.config import find_binary

    found = find_binary("ffmpeg")
    if found is None:
        pytest.skip("no ffmpeg.exe found (bin/ffmpeg.exe or PATH)")
    return found


def _fonts_dir() -> Path:
    fonts = assets_fonts_dir()
    if not (fonts / "Anton-Regular.ttf").is_file():
        pytest.skip("bundled fonts not fetched (python -m ash_captions.styles.fonts download)")
    return fonts


def _escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _card() -> Card:
    words = tuple(
        Word(text=text, start=start, end=start + 0.5)
        for text, start in sorted(WORD_STARTS.items(), key=lambda item: item[1])
    )
    return Card(words=words, start=0.0, end=CARD_END)


def _burn(style: Style, work: Path, name: str) -> Path:
    path = work / f"{name}.ass"
    write_ass([_card()], path, style, play_res=PLAY_RES)
    return path


def _frame(ass: Path, at: float, work: Path) -> Image.Image:
    png = work / f"{ass.stem}_{at:.2f}.png"
    command = [
        str(_ffmpeg_path()), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x808080:s={PLAY_RES[0]}x{PLAY_RES[1]}:r=50",
        "-vf", f"subtitles='{_escape(ass)}':fontsdir='{_escape(_fonts_dir())}'",
        "-ss", f"{at}", "-frames:v", "1", str(png),
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-2000:]
    return Image.open(png).convert("RGB")


def _measure(image: Image.Image, matches, window=None) -> dict | None:
    """The bounding box of every pixel in ``window`` passing ``matches``,
    plus the leftmost x of its top and bottom fifths (which is how a lean
    is measured)."""
    x0, y0, x1, y1 = window or (0, 0, image.width, image.height)
    rows: dict[int, list[int]] = {}
    count = 0
    for y in range(max(0, y0), min(image.height, y1)):
        for x in range(max(0, x0), min(image.width, x1)):
            if matches(image.getpixel((x, y))):
                rows.setdefault(y, []).append(x)
                count += 1
    if not rows:
        return None
    ys = sorted(rows)
    xs_min = min(min(row) for row in rows.values())
    xs_max = max(max(row) for row in rows.values())
    fifth = max(1, len(ys) // 5)
    return {
        "x": xs_min, "y": ys[0], "w": xs_max - xs_min, "h": ys[-1] - ys[0],
        "cx": (xs_min + xs_max) / 2, "cy": (ys[0] + ys[-1]) / 2, "n": count,
        "top_left": min(min(rows[y]) for y in ys[:fifth]),
        "bottom_left": min(min(rows[y]) for y in ys[-fifth:]),
    }


def _exactly(colour, tolerance=26):
    return lambda pixel: all(abs(a - b) <= tolerance for a, b in zip(pixel, colour, strict=True))


def _window(slot, pad_x=380, pad_y=240):
    cx, cy = slot.x * PLAY_RES[0], slot.y * PLAY_RES[1]
    return (int(cx - pad_x), int(cy - pad_y), int(cx + pad_x), int(cy + pad_y))


@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("free_real")


@pytest.fixture(scope="module")
def fixture_style() -> Style:
    return Style.from_dict(FIXTURE)


@pytest.fixture(scope="module")
def fixture_ass(fixture_style, work) -> Path:
    return _burn(fixture_style, work, "fixture")


@pytest.fixture(scope="module")
def settled(fixture_ass, work) -> Image.Image:
    """Every word arrived and settled: 2.8s into a card that ends at 3.0."""
    return _frame(fixture_ass, 2.8, work)


# ---------------------------------------------------------------------------
# 1. four treatments inside one caption
# ---------------------------------------------------------------------------


def test_four_words_are_drawn_in_four_colours_at_four_places(settled, fixture_style):
    slots = fixture_style.layout.slots
    boxes = {}
    for index, (word, colour) in SLOT_WORD.items():
        found = _measure(settled, _exactly(colour), _window(slots[index]))
        assert found is not None and found["n"] > 300, (word, found)
        boxes[word] = found
    # Each word is centred on its own slot, in PlayRes pixels.
    for index, (word, _colour) in SLOT_WORD.items():
        slot = slots[index]
        assert abs(boxes[word]["cx"] - slot.x * PLAY_RES[0]) <= 40, (word, boxes[word])
        assert abs(boxes[word]["cy"] - slot.y * PLAY_RES[1]) <= 40, (word, boxes[word])
    # ...and no two of them overlap vertically.
    ordered = sorted(boxes.values(), key=lambda box: box["y"])
    for above, below in zip(ordered, ordered[1:], strict=False):
        assert above["y"] + above["h"] < below["y"], (above, below)


def test_the_slot_scale_really_changes_the_glyph_size(settled, fixture_style):
    slots = fixture_style.layout.slots
    heights = {}
    for index, (word, colour) in SLOT_WORD.items():
        heights[word] = _measure(settled, _exactly(colour), _window(slots[index]))["h"]
    # Glyph heights rank exactly as the slot scales do (2.30 > 1.15 >
    # 0.75 > 0.55). Only the extremes get a ratio: the four words have
    # different ascenders and descenders, so their heights are ordered
    # by scale but not proportional to it.
    by_scale = [SLOT_WORD[index][0] for index in (1, 2, 3, 0)]
    ordered = [heights[word] for word in by_scale]
    assert ordered == sorted(ordered, reverse=True), heights
    assert len(set(ordered)) == 4, heights
    assert heights[by_scale[0]] > 3.4 * heights[by_scale[-1]], heights


def test_an_italic_slot_really_leans(settled, fixture_style):
    """Neither bundled face has an italic cut, so `\\i1` is libass's
    synthetic slant -- worth proving it is not silently a no-op."""
    slots = fixture_style.layout.slots
    white = _exactly((0xFF, 0xFF, 0xFF))
    leaning = _measure(settled, white, _window(slots[0]))  # italic slot
    upright = _measure(settled, white, _window(slots[3]))  # same colour, no lean
    assert leaning["top_left"] - leaning["bottom_left"] >= 6, leaning
    assert abs(upright["top_left"] - upright["bottom_left"]) <= 2, upright


# ---------------------------------------------------------------------------
# 2. the words accumulate
# ---------------------------------------------------------------------------


def _words_on_screen(image: Image.Image, style: Style) -> set[str]:
    slots = style.layout.slots
    present = set()
    for index, (word, colour) in SLOT_WORD.items():
        found = _measure(image, _exactly(colour), _window(slots[index]))
        if found and found["n"] > 300:
            present.add(word)
    return present


@pytest.mark.parametrize(
    "at,expected",
    [
        (0.40, {"hill"}),
        (0.90, {"hill", "bolt"}),
        (1.40, {"hill", "bolt", "kite"}),
        (2.80, {"hill", "bolt", "kite", "dial"}),
    ],
)
def test_each_word_stays_while_the_next_arrives(fixture_ass, fixture_style, work, at, expected):
    """The whole point of ending every event at the card's end rather
    than at the next word's start."""
    assert _words_on_screen(_frame(fixture_ass, at, work), fixture_style) == expected


# ---------------------------------------------------------------------------
# 3. the entrances animate
# ---------------------------------------------------------------------------


def _yellowish(pixel):
    """Yellow part-way through its fade, blended into the grey."""
    red, green, blue = pixel
    return red > blue + 45 and green > blue + 30 and red > 140


def test_stretch_collapse_is_wider_early_and_snaps_back_by_120ms(fixture_ass, fixture_style, work):
    window = _window(fixture_style.layout.slots[1], pad_x=520, pad_y=300)
    start = WORD_STARTS[SLOT_WORD[1][0]]
    early = _measure(_frame(fixture_ass, start + 0.04, work), _yellowish, window)
    mid = _measure(_frame(fixture_ass, start + 0.08, work), _yellowish, window)
    settled = _measure(_frame(fixture_ass, start + 0.30, work), _yellowish, window)
    assert early and mid and settled
    assert early["w"] > 1.35 * settled["w"], (early["w"], settled["w"])
    assert settled["w"] < mid["w"] < early["w"], (early["w"], mid["w"], settled["w"])
    # Wide, not tall: \fscy is left alone, so the height never changes.
    assert abs(early["h"] - settled["h"]) <= 4, (early["h"], settled["h"])
    # It is done by 120ms and does not drift afterwards.
    later = _measure(_frame(fixture_ass, start + 0.60, work), _yellowish, window)
    assert (later["w"], later["h"]) == (settled["w"], settled["h"])


def test_fade_settle_shrinks_and_drops_into_place_by_240ms(fixture_ass, fixture_style, work):
    slot = fixture_style.layout.slots[0]
    window = _window(slot, pad_x=320, pad_y=220)
    whiteish = lambda pixel: all(channel > 150 for channel in pixel) and max(pixel) - min(pixel) < 30  # noqa: E731
    start = WORD_STARTS[SLOT_WORD[0][0]]
    early = _measure(_frame(fixture_ass, start + 0.08, work), whiteish, window)
    settled = _measure(_frame(fixture_ass, start + 0.40, work), whiteish, window)
    assert early and settled
    assert early["w"] > settled["w"], (early["w"], settled["w"])  # shrinking from ~110%
    assert early["y"] < settled["y"], (early["y"], settled["y"])  # and dropping into place
    at_240 = _measure(_frame(fixture_ass, start + 0.26, work), whiteish, window)
    assert (at_240["w"], at_240["y"]) == (settled["w"], settled["y"])


# ---------------------------------------------------------------------------
# 4. the shipped look
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reel_estate() -> Style:
    return list_styles(user_dir=shipped_styles_dir().parent / "no-user-styles-here")["REEL ESTATE"]


def test_the_shipped_reel_look_puts_the_number_big_and_yellow(reel_estate, work):
    """The reference phrase through the shipped look, on real pixels."""
    words = (
        Word(text="the", start=0.0, end=0.5),
        Word(text="2nd", start=0.5, end=1.0),
        Word(text="Highest", start=1.0, end=1.5),
        Word(text="residential", start=1.5, end=3.0),
    )
    path = work / "reel_estate.ass"
    write_ass([Card(words=words, start=0.0, end=3.0)], path, reel_estate, play_res=PLAY_RES)
    frame = _frame(path, 2.8, work)

    biggest = max(reel_estate.layout.slots, key=lambda slot: slot.scale)
    number = _measure(frame, _exactly((0xFF, 0xD4, 0x00)), _window(biggest, pad_x=500, pad_y=320))
    assert number is not None and number["n"] > 2000, number
    assert abs(number["cx"] - biggest.x * PLAY_RES[0]) <= 40, number
    assert abs(number["cy"] - biggest.y * PLAY_RES[1]) <= 40, number

    smallest = min(reel_estate.layout.slots, key=lambda slot: slot.scale)
    connector = _measure(frame, _exactly((0xFF, 0xFF, 0xFF)), _window(smallest, pad_x=260, pad_y=160))
    assert connector is not None, "the connector never rendered"
    assert number["h"] > 3 * connector["h"], (number["h"], connector["h"])

    # The number is drawn in the look's active colour and nothing else:
    # its own bounding box holds no pixel of the look's text colour, so
    # the slot's role really resolved through the palette.
    box = (number["x"], number["y"], number["x"] + number["w"], number["y"] + number["h"])
    assert _measure(frame, _exactly((0xFF, 0xFF, 0xFF)), box) is None
