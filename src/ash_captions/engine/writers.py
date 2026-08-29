"""Output writers: .srt, .ass (styled, word-by-word highlight) and .txt.

All render_* functions are pure (str in, str out) so timing/format logic
is testable without touching disk; write_* wraps each with a file write.

``render_ass``/``write_ass`` now consume a ``Style`` from
``ash_captions.styles`` (spec 7A) -- that package owns the real,
animated renderer (word pop, karaoke, boxes, entrances, glow, shake;
see ``ash_captions.styles.render``). This module just dispatches to it.

The legacy ``AssPreset`` dataclass and the ``CLEAN``/``POP`` instances
built from it are kept as-is, unmodified, for backward compatibility:
older callers (and ``tests/test_engine/test_writers.py``, which asserts
their exact historical output byte-for-byte) still pass an ``AssPreset``
in, and ``render_ass``/``write_ass`` still produce identical output for
one. Dispatch is by *type* (``isinstance``), which is not the kind of
per-style-name branching spec 7A.2 rules out -- it is a single, static
fork between "old preset object" and "new style object", checked once,
not a growing list of special cases per look.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..styles.render import DEFAULT_PLAY_RES as _STYLE_DEFAULT_PLAY_RES
from ..styles.render import render_ass as _render_ass_styled
from ..styles.render import write_ass as _write_ass_styled
from ..styles.schema import Style
from .rules import Card
from .transcribe import Segment

# ---------------------------------------------------------------------------
# .srt
# ---------------------------------------------------------------------------


def render_srt(cards: Sequence[Card]) -> str:
    """Render clean, line-per-card SRT captions."""
    blocks = [
        f"{index}\n{_format_srt_time(card.start)} --> {_format_srt_time(card.end)}\n{card.text}"
        for index, card in enumerate(cards, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_srt(cards: Sequence[Card], path: Path | str) -> Path:
    return _write(render_srt(cards), path)


def _format_srt_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


# ---------------------------------------------------------------------------
# .txt
# ---------------------------------------------------------------------------


def render_txt(segments: Sequence[Segment]) -> str:
    """Render the plain transcript, one segment of speech per line."""
    lines = [segment.text for segment in segments if segment.text]
    return "\n".join(lines) + ("\n" if lines else "")


def write_txt(segments: Sequence[Segment], path: Path | str) -> Path:
    return _write(render_txt(segments), path)


# ---------------------------------------------------------------------------
# .ass
# ---------------------------------------------------------------------------

RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class AssPreset:
    """Styling data for one .ass caption look. Presets are values of this
    type, never a branch in the renderer."""

    name: str
    font_name: str = "Arial"
    font_size: int = 72
    bold: bool = True
    primary_colour: RGB = (255, 255, 255)
    highlight_colour: RGB = (255, 215, 0)
    outline_colour: RGB = (0, 0, 0)
    back_colour: RGB = (0, 0, 0)
    outline_width: float = 3.0
    shadow: float = 0.0
    alignment: int = 2  # ASS numpad alignment: 2 = bottom-center
    margin_l: int = 60
    margin_r: int = 60
    margin_v: int = 120


# Client-safe: understated, near-white highlight, no heavy weight.
CLEAN = AssPreset(
    name="CLEAN",
    font_name="Arial",
    font_size=64,
    bold=False,
    primary_colour=(255, 255, 255),
    highlight_colour=(255, 241, 181),
    outline_colour=(0, 0, 0),
    outline_width=2.0,
    shadow=0.0,
    alignment=2,
)

# Short-form: bold, saturated highlight, heavier outline.
POP = AssPreset(
    name="POP",
    font_name="Montserrat",
    font_size=84,
    bold=True,
    primary_colour=(255, 255, 255),
    highlight_colour=(0, 255, 140),
    outline_colour=(0, 0, 0),
    outline_width=4.0,
    shadow=1.0,
    alignment=2,
)

DEFAULT_PLAY_RES = (1080, 1920)  # vertical short-form default


def render_ass(
    cards: Sequence[Card],
    preset: AssPreset | Style,
    *,
    play_res: tuple[int, int] = DEFAULT_PLAY_RES,
) -> str:
    """Render styled, word-by-word ASS captions with the active word highlighted.

    Accepts either a ``Style`` (``ash_captions.styles`` -- spec 7A's real,
    animated renderer: word pop, karaoke, boxes, entrances, glow, shake,
    letter spacing, position variants) or a legacy ``AssPreset``, for
    which this keeps the original behaviour byte-for-byte: each word in a
    card becomes its own Dialogue event spanning from that word's start to
    the next word's start (or the card's end, for the last word), with the
    active word wrapped in a color override tag using
    ``preset.highlight_colour`` and the rest at ``preset.primary_colour``.
    """
    if isinstance(preset, Style):
        return _render_ass_styled(cards, preset, play_res=play_res)

    width, height = play_res
    header = _ass_header(preset, width, height)
    events: list[str] = []
    for card in cards:
        events.extend(_card_dialogue_events(card, preset))
    return header + "\n".join(events) + ("\n" if events else "")


def write_ass(
    cards: Sequence[Card],
    path: Path | str,
    preset: AssPreset | Style,
    *,
    play_res: tuple[int, int] = DEFAULT_PLAY_RES,
) -> Path:
    if isinstance(preset, Style):
        return _write_ass_styled(cards, path, preset, play_res=play_res)
    return _write(render_ass(cards, preset, play_res=play_res), path)


def _card_dialogue_events(card: Card, preset: AssPreset) -> list[str]:
    words = card.words
    count = len(words)
    lines: list[str] = []
    for i, word in enumerate(words):
        start = word.start
        end = words[i + 1].start if i < count - 1 else card.end
        if end <= start:
            end = start + 0.01
        text = _render_word_line(words, active_index=i, preset=preset)
        lines.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"{preset.name},,0,0,0,,{text}"
        )
    return lines


def _render_word_line(words: tuple, *, active_index: int, preset: AssPreset) -> str:
    primary = _ass_override_colour(preset.primary_colour)
    highlight = _ass_override_colour(preset.highlight_colour)
    parts = []
    for i, word in enumerate(words):
        if i == active_index:
            parts.append(f"{{\\c{highlight}}}{word.text}{{\\c{primary}}}")
        else:
            parts.append(word.text)
    return " ".join(parts)


def _ass_header(preset: AssPreset, width: int, height: int) -> str:
    primary = _ass_style_colour(preset.primary_colour)
    secondary = _ass_style_colour(preset.highlight_colour)
    outline = _ass_style_colour(preset.outline_colour)
    back = _ass_style_colour(preset.back_colour)
    bold = -1 if preset.bold else 0
    style = (
        f"Style: {preset.name},{preset.font_name},{preset.font_size},"
        f"{primary},{secondary},{outline},{back},{bold},0,0,0,100,100,0,0,"
        f"1,{preset.outline_width},{preset.shadow},{preset.alignment},"
        f"{preset.margin_l},{preset.margin_r},{preset.margin_v},1"
    )
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _ass_style_colour(rgb: RGB, alpha: int = 0) -> str:
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ass_override_colour(rgb: RGB) -> str:
    r, g, b = rgb
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _format_ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    total_cs = round(seconds * 100)
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def _write(content: str, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
