"""ASS text-format helpers for the style renderer: the [Script Info] /
[V4+ Styles] header, Style lines, colour and timestamp conversion.

Split out of ``render.py`` so that module stays about *effects*; nothing
here decides how a word animates. Colour conversion: "#RRGGBB"/"#RRGGBBAA"
-> ASS's ``&H..BGR..`` forms (ASS alpha is inverted from CSS: 00 opaque).
"""
from __future__ import annotations

from .schema import Style

# ASS "numpad" alignment: row from the vertical position (1-3 bottom, 4-6
# middle, 7-9 top), column from the horizontal align (left, centre, right).
_ROW_BASE = {"bottom": 1, "lower_third": 1, "center": 4, "top": 7}
_COLUMN_OFFSET = {"left": 0, "center": 1, "right": 2}


def ass_alignment(position: str, align: str = "center") -> int:
    return _ROW_BASE[position] + _COLUMN_OFFSET.get(align, 1)


def outline_width(style: Style) -> int:
    """The base Style's Outline column -- also the \\bord a glow restores."""
    return max(1, round(style.size * 0.055))


def box_padding(style: Style) -> int:
    """The Outline column of a BorderStyle=3 style, which is what pads a
    box. MEASURED: it adds its own value to each side, linearly -- 132px
    of text became 148, 156, 172 and 204 wide at padding 4, 8, 16 and 32.

    The 8px floor was here before ``box.padding`` was a control and is
    kept: a box thinner than that reads as a rendering fault rather than
    a design, and at small sizes 0.28 of the size falls under it.
    """
    return max(8, round(style.size * style.box.padding))


def shadow_visible(style: Style) -> bool:
    """A shadow is drawn only when it has both a colour and a distance.

    The colour test is the exact string the renderer has always used --
    widening it would turn the shadow on for looks that never had one.
    """
    return style.colors.shadow.upper() != "#00000000" and style.shadow.distance > 0


def shadow_offset(style: Style) -> tuple[float, float]:
    """Where the shadow falls, in script pixels.

    Degrees with 0 to the right and increasing clockwise, so +y is down
    the screen and 45 is down-right -- the one place ASS's own Shadow
    column could put it, which is why it is the default.

    ``distance`` is how far the shadow travels, not how far it moves on
    each axis. That distinction is the whole reason the default is 2.83
    and not 2: ASS's ``Shadow: 2`` offsets by 2 *on both axes*, whose
    real distance is 2 times the square root of 2. Getting this wrong
    would have quietly pulled every shipped look's shadow in to (1.41,
    1.41) while claiming nothing had changed.

    Both values are rounded here, not at the caller: cos(90 degrees) is
    6.1e-17 rather than 0, which formats as "0.00" on one axis and "0" on
    another, and negative zero prints as "-0".
    """
    import math

    radians = math.radians(style.shadow.angle)
    dx = round(style.shadow.distance * math.cos(radians), 2)
    dy = round(style.shadow.distance * math.sin(radians), 2)
    return (dx + 0.0 if dx else 0.0, dy + 0.0 if dy else 0.0)


def shadow_tags(style: Style) -> str:
    """``\\xshad``/``\\yshad`` for the leading override block, or "".

    MEASURED 2026-09-10: a Style-level ``Shadow: N`` and an inline
    ``\\xshadN\\yshadN`` are byte-identical at 48, 90 and 140px and at
    distance 1, 2, 4 and 8. That equivalence is the whole reason the
    renderer can move to the inline pair -- and so gain an angle -- with
    no shipped look rendering one pixel differently.
    """
    if not shadow_visible(style):
        return ""
    dx, dy = shadow_offset(style)
    return f"\\xshad{_num(dx)}\\yshad{_num(dy)}"


def _num(value: float) -> str:
    """Trim a float the way the rest of the renderer does: 2 not 2.0,
    283.5 not 283.50. The drift test compares these strings against the
    JavaScript port character for character."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------


def ass_header(style: Style, base_name: str, box_name: str, width: int, height: int) -> str:
    alignment = ass_alignment(style.layout.position, getattr(style.layout, "align", "center"))
    outline = outline_width(style)
    # The shadow is drawn by the inline pair in the leading override now,
    # not by this column, because a column has no angle. The two render
    # identically -- see shadow_tags.
    padding = box_padding(style)
    # card_box: the base style itself is the bar -- every word of the
    # caption sits on one box, the active word differing only by colour.
    card_box = style.active_word.effect == "card_box"
    base_style = _style_field(
        name=base_name,
        font=style.font,
        size=style.size,
        primary=style.colors.active,
        secondary=style.colors.text,
        outline_colour=style.colors.box if card_box else style.colors.outline,
        back_colour=style.colors.box if card_box else style.colors.shadow,
        border_style=3 if card_box else 1,
        outline_width=padding if card_box else outline,
        shadow=0,
        alignment=alignment,
        layout=style.layout,
    )
    box_style = _style_field(
        name=box_name,
        font=style.font,
        size=style.size,
        primary=style.colors.active,
        secondary=style.colors.active,
        outline_colour=style.colors.box,
        back_colour=style.colors.box,
        border_style=3,
        outline_width=padding,
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
        f"{ass_style_colour(primary)},{ass_style_colour(secondary)},"
        f"{ass_style_colour(outline_colour)},{ass_style_colour(back_colour)},"
        f"0,0,0,0,100,100,0,0,"
        f"{border_style},{outline_width},{shadow},{alignment},"
        f"{layout.margin_l},{layout.margin_r},{layout.margin_v},1"
    )


def safe_style_name(name: str) -> str:
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


def ass_style_colour(colour: str) -> str:
    """``&HAABBGGRR`` for a [V4+ Styles] colour column. ASS alpha is
    inverted from CSS: 00 is opaque, FF is fully transparent."""
    r, g, b, a = _parse_hex(colour)
    ass_alpha = 255 - a
    return f"&H{ass_alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_inline_colour(colour: str) -> str:
    """``&HBBGGRR&`` for an inline ``\\c``/``\\1c``/``\\3c`` override tag."""
    r, g, b, _a = _parse_hex(colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def format_ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    total_cs = round(seconds * 100)
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"
