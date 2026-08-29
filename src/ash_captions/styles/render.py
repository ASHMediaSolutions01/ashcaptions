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
  * glow                     -- ``\\blur`` / a widened, colour-matched
                                 ``\\bord``
  * letter spacing, all-caps -- ``\\fsp``, ``str.upper()``
  * position variants        -- ``\\an`` + margins

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

from collections.abc import Sequence

from ..engine.rules import Card
from .schema import Style

DEFAULT_PLAY_RES = (1080, 1920)  # vertical short-form default

# Fullwidth lookalikes: visually close to the ASCII originals, structurally
# inert to the ASS/libass tag parser.
_ESCAPE_MAP = {"{": "｛", "}": "｝", "\\": "＼"}

_ALIGNMENT = {"bottom": 2, "lower_third": 2, "center": 5, "top": 8}

_RISE_OFFSET_PX = 46
_SLIDE_OFFSET_PX = 160


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def render_ass(
    cards: Sequence[Card],
    style: Style,
    *,
    play_res: tuple[int, int] = DEFAULT_PLAY_RES,
) -> str:
    """Render animated, word-by-word ASS captions for ``style``."""
    width, height = play_res
    base_name = _safe_style_name(style.name)
    box_name = base_name + "_BOX"
    header = _ass_header(style, base_name, box_name, width, height)

    events: list[str] = []
    for card in cards:
        events.extend(_card_events(card, style, base_name, box_name, width, height))
    return header + "\n".join(events) + ("\n" if events else "")


def write_ass(
    cards: Sequence[Card],
    path,
    style: Style,
    *,
    play_res: tuple[int, int] = DEFAULT_PLAY_RES,
):
    from pathlib import Path

    content = render_ass(cards, style, play_res=play_res)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# per-card dispatch -- branches on style.active_word.effect, never on name
# ---------------------------------------------------------------------------


def _card_events(
    card: Card, style: Style, base_name: str, box_name: str, width: int, height: int
) -> list[str]:
    effect = style.active_word.effect
    if effect == "karaoke":
        return _karaoke_events(card, style, base_name, width, height)
    if effect in ("box", "scale_box"):
        return _box_events(card, style, box_name, width, height)
    return _standard_events(card, style, base_name, width, height)


def _standard_events(card: Card, style: Style, style_name: str, width: int, height: int) -> list[str]:
    words = card.words
    count = len(words)
    x, y = _anchor_xy(style, width, height)
    lines: list[str] = []
    for i, word in enumerate(words):
        start = word.start
        end = words[i + 1].start if i < count - 1 else card.end
        if end <= start:
            end = start + 0.01
        event_ms = max(1, round((end - start) * 1000))
        text = _line_text(words, active_index=i, style=style)
        leading = _leading_override(
            style, x, y, is_first=(i == 0), is_last=(i == count - 1), event_ms=event_ms
        )
        dialogue_text = f"{{{leading}}}{text}" if leading else text
        lines.append(_dialogue_line(start, end, style_name, dialogue_text))
    return lines


def _box_events(card: Card, style: Style, style_name: str, width: int, height: int) -> list[str]:
    """One word at a time, boxed -- see the module docstring for why."""
    words = card.words
    count = len(words)
    x, y = _anchor_xy(style, width, height)
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
            style, x, y, is_first=(i == 0), is_last=(i == count - 1), event_ms=event_ms
        )
        body = f"{scale_tags}{text}"
        dialogue_text = f"{{{leading}}}{body}" if leading else body
        lines.append(_dialogue_line(start, end, style_name, dialogue_text))
    return lines


def _karaoke_events(card: Card, style: Style, style_name: str, width: int, height: int) -> list[str]:
    """One Dialogue event for the whole card: ``\\kf`` needs a single run
    of text so libass can sweep the fill across it (spec 7A.1)."""
    words = card.words
    x, y = _anchor_xy(style, width, height)
    event_ms = max(1, round((card.end - card.start) * 1000))
    parts = []
    for word in words:
        duration_cs = max(1, round((word.end - word.start) * 100))
        parts.append(f"{{\\kf{duration_cs}}}{_prepare_word_text(word.text, style)}")
    body = " ".join(parts)
    leading = _leading_override(style, x, y, is_first=True, is_last=True, event_ms=event_ms)
    dialogue_text = f"{{{leading}}}{body}" if leading else body
    return [_dialogue_line(card.start, card.end, style_name, dialogue_text)]


def _dialogue_line(start: float, end: float, style_name: str, text: str) -> str:
    return f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},{style_name},,0,0,0,,{text}"


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
    text_colour = _ass_inline_colour(style.colors.text)
    active_colour = _ass_inline_colour(style.colors.active)
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
    if effect == "pop":
        scale = round(style.active_word.scale * 100)
        d = _POP_HALF_MS
        open_tags = f"\\c{active_colour}\\t(0,{d},\\fscx{scale}\\fscy{scale})\\t({d},{2 * d},\\fscx100\\fscy100)"
        close_tags = f"\\c{text_colour}\\fscx100\\fscy100"
    elif effect == "shake":
        q = _SHAKE_QUARTER_MS
        open_tags = (
            f"\\c{active_colour}"
            f"\\t(0,{q},\\frz-4)\\t({q},{2 * q},\\frz4)"
            f"\\t({2 * q},{3 * q},\\frz-2)\\t({3 * q},{4 * q},\\frz0)"
        )
        close_tags = f"\\c{text_colour}\\frz0"
    elif effect == "glow":
        outline_glow = _ass_inline_colour(style.colors.active)
        open_tags = f"\\c{active_colour}\\3c{outline_glow}\\blur4\\be1"
        outline = _ass_inline_colour(style.colors.outline)
        close_tags = f"\\c{text_colour}\\3c{outline}\\blur0\\be0"
    else:  # "none" -- colour swap only
        open_tags = f"\\c{active_colour}"
        close_tags = f"\\c{text_colour}"
    return open_tags, close_tags


_POP_HALF_MS = 90
_SHAKE_QUARTER_MS = 45


def _pop_scale_tags(style: Style, event_ms: int) -> str:
    scale = round(style.active_word.scale * 100)
    d = min(_POP_HALF_MS, max(1, event_ms // 2))
    return f"{{\\t(0,{d},\\fscx{scale}\\fscy{scale})\\t({d},{2 * d},\\fscx100\\fscy100)}}"


# ---------------------------------------------------------------------------
# entrance / exit (leading override block: \fsp + \fad or \move)
# ---------------------------------------------------------------------------


def _leading_override(
    style: Style, x: float, y: float, *, is_first: bool, is_last: bool, event_ms: int
) -> str:
    tags: list[str] = []
    if style.letter_spacing:
        tags.append(f"\\fsp{_num(style.letter_spacing)}")
    if is_first:
        tags.append(_entrance_tag(style, x, y))
    if is_last:
        tags.append(_exit_tag(style, x, y, event_ms))
    return "".join(t for t in tags if t)


def _entrance_tag(style: Style, x: float, y: float) -> str:
    effect = style.entrance.effect
    duration_ms = style.entrance.duration_ms
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


def _anchor_xy(style: Style, width: int, height: int) -> tuple[float, float]:
    an = _ALIGNMENT[style.layout.position]
    x = width / 2
    if an == 2:
        y = height - style.layout.margin_v
    elif an == 8:
        y = style.layout.margin_v
    else:
        y = height / 2
    return x, y


def _num(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------


def _ass_header(style: Style, base_name: str, box_name: str, width: int, height: int) -> str:
    alignment = _ALIGNMENT[style.layout.position]
    outline_width = max(1, round(style.size * 0.055))
    shadow_width = 2 if style.colors.shadow.upper() not in ("#00000000",) else 0

    base_style = _style_field(
        name=base_name,
        font=style.font,
        size=style.size,
        primary=style.colors.active,
        secondary=style.colors.text,
        outline_colour=style.colors.outline,
        back_colour=style.colors.shadow,
        border_style=1,
        outline_width=outline_width,
        shadow=shadow_width,
        alignment=alignment,
        layout=style.layout,
    )
    box_padding = max(8, round(style.size * 0.28))
    box_style = _style_field(
        name=box_name,
        font=style.font,
        size=style.size,
        primary=style.colors.text,
        secondary=style.colors.text,
        outline_colour=style.colors.box,
        back_colour=style.colors.box,
        border_style=3,
        outline_width=box_padding,
        shadow=0,
        alignment=alignment,
        layout=style.layout,
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
        f"{base_style}\n"
        f"{box_style}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _style_field(
    *,
    name: str,
    font: str,
    size: int,
    primary: str,
    secondary: str,
    outline_colour: str,
    back_colour: str,
    border_style: int,
    outline_width: int,
    shadow: int,
    alignment: int,
    layout,
) -> str:
    return (
        f"Style: {name},{font},{size},"
        f"{_ass_style_colour(primary)},{_ass_style_colour(secondary)},"
        f"{_ass_style_colour(outline_colour)},{_ass_style_colour(back_colour)},"
        f"0,0,0,0,100,100,0,0,"
        f"{border_style},{outline_width},{shadow},{alignment},"
        f"{layout.margin_l},{layout.margin_r},{layout.margin_v},1"
    )


def _safe_style_name(name: str) -> str:
    # ASS Style names can't contain a comma (the format is comma-delimited)
    # and shouldn't collide with the "_BOX" companion style suffix.
    return name.replace(",", "").replace(" ", "_") or "STYLE"


# ---------------------------------------------------------------------------
# colour conversion: "#RRGGBB"/"#RRGGBBAA" -> ASS's &H..BGR.. forms
# ---------------------------------------------------------------------------


def _parse_hex(colour: str) -> tuple[int, int, int, int]:
    body = colour.lstrip("#")
    if len(body) == 6:
        r, g, b = (int(body[i : i + 2], 16) for i in (0, 2, 4))
        a = 255
    else:
        r, g, b, a = (int(body[i : i + 2], 16) for i in (0, 2, 4, 6))
    return r, g, b, a


def _ass_style_colour(colour: str) -> str:
    """``&HAABBGGRR`` for a [V4+ Styles] colour column. ASS alpha is
    inverted from CSS: 00 is opaque, FF is fully transparent."""
    r, g, b, a = _parse_hex(colour)
    ass_alpha = 255 - a
    return f"&H{ass_alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ass_inline_colour(colour: str) -> str:
    """``&HBBGGRR&`` for an inline ``\\c``/``\\1c``/``\\3c`` override tag."""
    r, g, b, _a = _parse_hex(colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _format_ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    total_cs = round(seconds * 100)
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"
