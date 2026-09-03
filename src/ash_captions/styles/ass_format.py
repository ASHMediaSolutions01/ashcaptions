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


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------


def ass_header(style: Style, base_name: str, box_name: str, width: int, height: int) -> str:
    alignment = ass_alignment(style.layout.position, getattr(style.layout, "align", "center"))
    outline = outline_width(style)
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
        outline_width=outline,
        shadow=shadow_width,
        alignment=alignment,
        layout=style.layout,
    )
    box_padding = max(8, round(style.size * 0.28))
    box_style = _style_field(
        name=box_name,
        font=style.font,
        size=style.size,
        primary=style.colors.active,
        secondary=style.colors.active,
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
