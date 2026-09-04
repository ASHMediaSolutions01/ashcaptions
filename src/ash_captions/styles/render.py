"""Turns (cards, style) into animated ASS -- the core of the style engine
(spec 7A.1, 7A.2).

Every effect in the spec's table is implemented with real libass override
tags; nothing here is a stub. The one hard rule, load-bearing for the
whole "styles are data" premise: **this module never branches on
``style.name``.** Every decision below reads an *enum field*
(``active_word.effect``, ``entrance.effect``, ``layout.position`` ...),
never the style's identity. ``test_render_no_name_branching`` in
``tests/test_styles`` renders every shipped style through the exact same
code path and just checks none of them raise or produce empty output --
proving a brand-new style JSON works with zero code changes.

Effect -> tag mapping (spec 7A.1):

  * active-word scale       -- ``\\t(0,ms,\\fscx..\\fscy..)`` back to 100
  * karaoke fill             -- ``\\kf`` (one Dialogue event per *card*,
                                 not per word -- see ``_karaoke_events``)
  * box behind active word   -- ``BorderStyle=3`` + ``BackColour`` on a
                                 companion Style, auto-sized by libass to
                                 whatever text it's given. Because
                                 BorderStyle is a Style-level property (no
                                 override tag can change it mid-line), a
                                 boxed card shows one word at a time in
                                 that companion style rather than a full
                                 sentence with one word boxed -- which is
                                 also what real Hormozi-style captions
                                 look like. See ``_box_events``.
  * entrance / exit          -- ``\\fad`` for "fade"; ``\\move`` for
                                 "rise"/"slide", applied only to the
                                 card's first (entrance) and last (exit)
                                 Dialogue event
  * shake                    -- a ``\\t`` chain on ``\\frz``
  * glow                     -- two Dialogue lines per word event: a
                                 layer-0 halo (other words transparent,
                                 active word hollow with a wide, blurred
                                 outline in the active colour -- see
                                 ``render_glow``) under a layer-1 line that
                                 is exactly the ``pop`` rendering
  * letter spacing, all-caps -- ``\\fsp``, ``str.upper()``
  * position variants        -- ``\\an`` + margins; with an explicit
                                 ``anchor`` (the Studio's drag, v0.5) every
                                 event is pinned there by ``\\pos`` or a
                                 ``\\move`` that starts/ends there, the
                                 ``\\an`` code unchanged

Play resolution: ``PlayResX``/``PlayResY`` must match the video the
captions are burned into, or libass scales every size and margin by the
ratio (a 1080x1920 script on a 1920x1080 video renders at half the
intended font size, in the wrong place). ``render_ass``/``write_ass``
take ``play_res=(width, height)``; callers that know the video (the job
runner, which probes it; the preview adapter) must pass it. ``None``
falls back to ``DEFAULT_PLAY_RES`` -- the vertical short-form default --
for callers that genuinely have no video, like the style editor's
offline validation.

Text escaping: a literal ``{``, ``}`` or ``\\`` in a transcript is
replaced with its fullwidth Unicode lookalike (``｛ ｝ ＼``) before any
tag is built. libass has no escape sequence for a literal brace or
backslash inside the Text field -- a raw one would open an override
block (or, for ``\\N``/``\\n``/``\\h``, be interpreted as a control code)
that was never meant to be there. That is a rendering bug on ordinary
transcripts ("use the {name} field") and an injection vector on
adversarial ones, so every word goes through ``_escape_ass_text`` before
it touches an f-string.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from ..engine.rules import Card
from .ass_format import (
    ass_alignment,
    ass_header,
    ass_inline_colour,
    format_ass_time,
    safe_style_name,
)
from .render_glow import HALO_LAYER, POP_HALF_MS, TEXT_LAYER, halo_line_text, scale_transform_tags
from .schema import Style

DEFAULT_PLAY_RES = (1080, 1920)  # vertical short-form default

# Fullwidth lookalikes: visually close to the ASCII originals, structurally
# inert to the ASS/libass tag parser.
_ESCAPE_MAP = {"{": "｛", "}": "｝", "\\": "＼"}


_RISE_OFFSET_PX = 46
_SLIDE_OFFSET_PX = 160


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def render_ass(
    cards: Sequence[Card],
    style: Style,
    *,
    play_res: tuple[int, int] | None = None,
    anchor: tuple[float, float] | None = None,
) -> str:
    """Render animated, word-by-word ASS captions for ``style``.

    ``play_res`` is the ``(width, height)`` of the video the captions
    will be burned into (see the module docstring); ``None`` means
    ``DEFAULT_PLAY_RES``. ``anchor`` is an absolute ``(x, y)`` in those
    PlayRes pixels: when set, every Dialogue event is pinned to it with
    ``\\pos`` (static) or a ``\\move`` that ends there (rise/slide
    entrance) or starts there (exit), instead of relying on the Style
    line's margins; the Style line itself is untouched. ``None`` leaves
    the output exactly as it was without the feature.
    """
    width, height = _resolve_play_res(play_res)
    pinned = _resolve_anchor(anchor)
    base_name = safe_style_name(style.name)
    box_name = base_name + "_BOX"
    header = ass_header(style, base_name, box_name, width, height)

    events: list[str] = []
    for card in cards:
        events.extend(_card_events(card, style, base_name, box_name, width, height, pinned))
    return header + "\n".join(events) + ("\n" if events else "")


def write_ass(
    cards: Sequence[Card],
    path,
    style: Style,
    *,
    play_res: tuple[int, int] | None = None,
    anchor: tuple[float, float] | None = None,
):
    """``render_ass`` to a file. ``play_res``/``anchor`` as for ``render_ass``."""
    from pathlib import Path

    content = render_ass(cards, style, play_res=play_res, anchor=anchor)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out


def anchor_pixels(
    position: tuple[float, float] | None, play_res: tuple[int, int] | None
) -> tuple[float, float] | None:
    """The one place a stored caption position (fractions of the frame,
    ``(caption_x, caption_y)`` in [0, 1]) becomes the absolute anchor in
    PlayRes pixels that ``render_ass`` takes. ``None`` passes through;
    ``play_res`` ``None`` means ``DEFAULT_PLAY_RES``, the same default
    ``render_ass`` uses, so the two always agree."""
    if position is None:
        return None
    width, height = _resolve_play_res(play_res)
    try:
        fx, fy = position
        fx, fy = float(fx), float(fy)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"position must be a (caption_x, caption_y) pair, got {position!r}") from exc
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        raise ValueError(f"position fractions must be within [0, 1], got {position!r}")
    return fx * width, fy * height


def _resolve_play_res(play_res: tuple[int, int] | None) -> tuple[int, int]:
    if play_res is None:
        return DEFAULT_PLAY_RES
    width, height = play_res
    if int(width) <= 0 or int(height) <= 0:
        raise ValueError(f"play_res must be positive (width, height), got {play_res!r}")
    return int(width), int(height)


def _resolve_anchor(anchor: tuple[float, float] | None) -> tuple[float, float] | None:
    if anchor is None:
        return None
    try:
        x, y = anchor
        x, y = float(x), float(y)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"anchor must be an (x, y) pair in PlayRes pixels, got {anchor!r}") from exc
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"anchor must be finite, got {anchor!r}")
    return x, y


# ---------------------------------------------------------------------------
# per-card dispatch -- branches on style.active_word.effect, never on name
# ---------------------------------------------------------------------------


def _card_events(
    card: Card,
    style: Style,
    base_name: str,
    box_name: str,
    width: int,
    height: int,
    anchor: tuple[float, float] | None = None,
) -> list[str]:
    effect = style.active_word.effect
    if effect == "karaoke":
        return _karaoke_events(card, style, base_name, width, height, anchor)
    if effect in ("box", "scale_box"):
        return _box_events(card, style, box_name, width, height, anchor)
    return _standard_events(card, style, base_name, width, height, anchor)


def _standard_events(
    card: Card, style: Style, style_name: str, width: int, height: int, anchor: tuple[float, float] | None = None
) -> list[str]:
    words = card.words
    count = len(words)
    x, y = _anchor_xy(style, width, height, anchor)
    glow = style.active_word.effect == "glow"
    lines: list[str] = []
    for i, word in enumerate(words):
        start = word.start
        end = words[i + 1].start if i < count - 1 else card.end
        if end <= start:
            end = start + 0.01
        event_ms = max(1, round((end - start) * 1000))
        text = _line_text(words, active_index=i, style=style)
        leading = _leading_override(
            style, x, y, is_first=(i == 0), is_last=(i == count - 1), event_ms=event_ms, pinned=anchor is not None
        )
        prefix = f"{{{leading}}}" if leading else ""
        if glow:
            # Halo first (layer 0), the crisp pop-style text over it (layer
            # 1); both carry the same entrance/exit block -- see render_glow.
            prepared = [_prepare_word_text(w.text, style) for w in words]
            halo = halo_line_text(prepared, i, style)
            lines.append(_dialogue_line(start, end, style_name, prefix + halo, layer=HALO_LAYER))
            lines.append(_dialogue_line(start, end, style_name, prefix + text, layer=TEXT_LAYER))
        else:
            lines.append(_dialogue_line(start, end, style_name, prefix + text))
    return lines


def _box_events(
    card: Card, style: Style, style_name: str, width: int, height: int, anchor: tuple[float, float] | None = None
) -> list[str]:
    """One word at a time, boxed -- see the module docstring for why."""
    words = card.words
    count = len(words)
    x, y = _anchor_xy(style, width, height, anchor)
    lines: list[str] = []
    for i, word in enumerate(words):
        start = word.start
        end = words[i + 1].start if i < count - 1 else card.end
        if end <= start:
            end = start + 0.01
        event_ms = max(1, round((end - start) * 1000))
        text = _prepare_word_text(word.text, style)
        scale_tags = _pop_scale_tags(style, event_ms) if style.active_word.effect == "scale_box" else ""
        leading = _leading_override(
            style, x, y, is_first=(i == 0), is_last=(i == count - 1), event_ms=event_ms, pinned=anchor is not None
        )
        body = f"{scale_tags}{text}"
        dialogue_text = f"{{{leading}}}{body}" if leading else body
        lines.append(_dialogue_line(start, end, style_name, dialogue_text))
    return lines


def _karaoke_events(
    card: Card, style: Style, style_name: str, width: int, height: int, anchor: tuple[float, float] | None = None
) -> list[str]:
    """One Dialogue event for the whole card: ``\\kf`` needs a single run
    of text so libass can sweep the fill across it (spec 7A.1)."""
    words = card.words
    count = len(words)
    x, y = _anchor_xy(style, width, height, anchor)
    event_ms = max(1, round((card.end - card.start) * 1000))
    parts = []
    for i, word in enumerate(words):
        # Each \kf run must last until the *next* word starts (the card's
        # end for the last word), exactly like the per-word events: the
        # sweep is cumulative from the event start, so sizing runs by
        # word.end - word.start drops every inter-word gap and the fill
        # runs progressively ahead of the audio across the card.
        run_end = words[i + 1].start if i < count - 1 else card.end
        duration_cs = max(1, round((run_end - word.start) * 100))
        parts.append(f"{{\\kf{duration_cs}}}{_prepare_word_text(word.text, style)}")
    body = " ".join(parts)
    leading = _leading_override(style, x, y, is_first=True, is_last=True, event_ms=event_ms, pinned=anchor is not None)
    dialogue_text = f"{{{leading}}}{body}" if leading else body
    return [_dialogue_line(card.start, card.end, style_name, dialogue_text)]


def _dialogue_line(start: float, end: float, style_name: str, text: str, layer: int = 0) -> str:
    return f"Dialogue: {layer},{format_ass_time(start)},{format_ass_time(end)},{style_name},,0,0,0,,{text}"


# ---------------------------------------------------------------------------
# per-word text and inline effect tags
# ---------------------------------------------------------------------------


def _escape_ass_text(text: str) -> str:
    return "".join(_ESCAPE_MAP.get(ch, ch) for ch in text)


def _prepare_word_text(text: str, style: Style) -> str:
    if style.uppercase:
        text = text.upper()
    return _escape_ass_text(text)


def _line_text(words: tuple, *, active_index: int, style: Style) -> str:
    """Full sentence, active word wrapped with colour + its effect tags."""
    text_colour = ass_inline_colour(style.colors.text)
    active_colour = ass_inline_colour(style.colors.active)
    parts = []
    for i, word in enumerate(words):
        word_text = _prepare_word_text(word.text, style)
        if i == active_index:
            open_tags, close_tags = _active_word_tags(style, active_colour, text_colour)
            parts.append(f"{{{open_tags}}}{word_text}{{{close_tags}}}")
        else:
            parts.append(f"{{\\c{text_colour}}}{word_text}")
    return "".join(_join_words(parts))


def _join_words(parts: list[str]) -> list[str]:
    # Interleave a plain space between word runs so override blocks stay
    # adjacent to their word (a space inside an override block is inert,
    # but keeping it outside is simpler to read in the raw .ass).
    joined = []
    for i, part in enumerate(parts):
        if i > 0:
            joined.append(" ")
        joined.append(part)
    return joined


def _active_word_tags(style: Style, active_colour: str, text_colour: str) -> tuple[str, str]:
    effect = style.active_word.effect
    if effect in ("pop", "glow"):
        # glow's visible text is the pop rendering; its halo is a separate
        # layer-0 event built by render_glow.halo_line_text.
        open_tags = f"\\c{active_colour}{scale_transform_tags(style.active_word.scale)}"
        close_tags = f"\\c{text_colour}\\fscx100\\fscy100"
    elif effect == "shake":
        q = _SHAKE_QUARTER_MS
        open_tags = (
            f"\\c{active_colour}"
            f"\\t(0,{q},\\frz-4)\\t({q},{2 * q},\\frz4)"
            f"\\t({2 * q},{3 * q},\\frz-2)\\t({3 * q},{4 * q},\\frz0)"
        )
        close_tags = f"\\c{text_colour}\\frz0"
    else:  # "none" -- colour swap only
        open_tags = f"\\c{active_colour}"
        close_tags = f"\\c{text_colour}"
    return open_tags, close_tags


_SHAKE_QUARTER_MS = 45


def _pop_scale_tags(style: Style, event_ms: int) -> str:
    scale = round(style.active_word.scale * 100)
    d = min(POP_HALF_MS, max(1, event_ms // 2))
    return f"{{\\t(0,{d},\\fscx{scale}\\fscy{scale})\\t({d},{2 * d},\\fscx100\\fscy100)}}"


# ---------------------------------------------------------------------------
# entrance / exit (leading override block: \fsp + \fad or \move)
# ---------------------------------------------------------------------------


def _leading_override(
    style: Style,
    x: float,
    y: float,
    *,
    is_first: bool,
    is_last: bool,
    event_ms: int,
    pinned: bool = False,
) -> str:
    tags: list[str] = []
    if style.letter_spacing:
        tags.append(f"\\fsp{_num(style.letter_spacing)}")

    entrance_tag = _entrance_tag(style, x, y, event_ms) if is_first else ""
    exit_tag = _exit_tag(style, x, y, event_ms) if is_last else ""

    if entrance_tag and exit_tag and _tag_kind(entrance_tag) == _tag_kind(exit_tag):
        # A single event carries both entrance and exit -- a one-word
        # card, or a karaoke card (one event per card, not per word). Two
        # \fad or two \move tags on the same line don't compose in
        # libass; the second one silently wins and the first is lost.
        # \fad(t1,t2) already fades in *and* out in one call, so merge
        # those into one tag; two \move effects can't merge the same way,
        # so entrance wins and the exit motion is dropped for that event.
        if _tag_kind(entrance_tag) == "fad":
            entrance_ms = min(style.entrance.duration_ms, event_ms) if style.entrance.effect == "fade" else 0
            exit_ms = min(style.exit.duration_ms, event_ms) if style.exit.effect == "fade" else 0
            if entrance_ms + exit_ms > event_ms:
                # Both halves can't fit; split the event between them so
                # the word is never invisible for its whole duration.
                entrance_ms = event_ms // 2
                exit_ms = event_ms - entrance_ms
            tags.append(f"\\fad({entrance_ms},{exit_ms})")
        else:
            tags.append(entrance_tag)
    else:
        if entrance_tag:
            tags.append(entrance_tag)
        if exit_tag:
            tags.append(exit_tag)
    if pinned and not any(t.startswith("\\move(") for t in tags):
        # No motion on this event, so nothing else places it: pin it. A
        # \move already starts or ends at (x, y); \pos and \move on one
        # line don't compose in libass, so never emit both.
        tags.append(f"\\pos({_num(x)},{_num(y)})")
    return "".join(t for t in tags if t)


def _tag_kind(tag: str) -> str:
    return "fad" if tag.startswith("\\fad(") else "move"


def _entrance_tag(style: Style, x: float, y: float, event_ms: int) -> str:
    effect = style.entrance.effect
    # The first per-word event of a card is often shorter than the
    # entrance (a 120ms word under a 160ms rise): \fad/\move past the
    # event's end are simply cut off, so the word never reaches full
    # opacity or its resting position. Clamp, as _exit_tag does.
    duration_ms = min(style.entrance.duration_ms, event_ms)
    if effect == "fade" and duration_ms:
        return f"\\fad({duration_ms},0)"
    if effect in ("rise", "slide") and duration_ms:
        dx, dy = (0, _RISE_OFFSET_PX) if effect == "rise" else (_SLIDE_OFFSET_PX, 0)
        x1, y1 = x + dx, y + dy
        return f"\\move({_num(x1)},{_num(y1)},{_num(x)},{_num(y)},0,{duration_ms})"
    return ""


def _exit_tag(style: Style, x: float, y: float, event_ms: int) -> str:
    effect = style.exit.effect
    duration_ms = min(style.exit.duration_ms, event_ms)
    if effect == "fade" and duration_ms:
        return f"\\fad(0,{duration_ms})"
    if effect in ("rise", "slide") and duration_ms:
        # \move's t1/t2 are relative to *this event's own* start, so the
        # motion is pinned to the tail of the event regardless of how
        # long the event runs -- see the module docstring on why exit only
        # applies to a card's last Dialogue event.
        dx, dy = (0, -_RISE_OFFSET_PX) if effect == "rise" else (-_SLIDE_OFFSET_PX, 0)
        x2, y2 = x + dx, y + dy
        t1 = max(0, event_ms - duration_ms)
        return f"\\move({_num(x)},{_num(y)},{_num(x2)},{_num(y2)},{t1},{event_ms})"
    return ""


def _anchor_xy(style: Style, width: int, height: int, override: tuple[float, float] | None = None) -> tuple[float, float]:
    """Where libass would place the caption's anchor for this style's
    ``\\an`` and margins -- the point ``\\move``/``\\pos`` animations start
    from and return to. It must agree with the Style line's alignment for
    all nine numpad positions, or an animated caption lands somewhere
    other than a static one would (a TOP RIGHT style once rendered its
    sliding captions dead centre because only ``\\an2`` and ``\\an8`` were
    handled here). ``override`` (PlayRes pixels, from ``anchor_pixels``)
    replaces the computed point outright: the Studio's dragged position,
    with the same ``\\an`` so a left-aligned look stays left-aligned
    around it."""
    if override is not None:
        return float(override[0]), float(override[1])
    an = ass_alignment(style.layout.position, getattr(style.layout, "align", "center"))
    row, column = (an - 1) // 3, (an - 1) % 3  # numpad: rows bottom/middle/top, columns left/centre/right
    if row == 0:
        y = height - style.layout.margin_v
    elif row == 2:
        y = style.layout.margin_v
    else:
        y = height / 2
    if column == 0:
        x = style.layout.margin_l
    elif column == 2:
        x = width - style.layout.margin_r
    else:
        x = width / 2
    return x, y


def _num(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"
