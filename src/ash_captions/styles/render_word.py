"""One word's text and tags: the look's, and the editor's own.

``line_text`` (below, moved here from ``render`` when this module was
added) already wraps every word of a caption in its own ``{...}`` override
block; ``render._box_events`` and ``render._karaoke_events`` do the same one
word at a time. That block is the whole seam for the per-word styling of
v0.6 design section 2. This module builds the tags one ``WordStyle``
contributes to it, and the tags that put the line back afterwards, so the
words around it are untouched.

Two rules the rest of the renderer relies on:

  * **The word wins.** The look's tags are emitted first and the word's
    last inside the same block, so ``\\c`` from the override lands after
    ``\\c`` from the look.
  * **No override, no bytes.** Every helper here returns empty strings for
    ``None``, which is what makes ``render_ass(..., word_styles=None)``
    byte-identical to the renderer before this feature existed. The
    goldens in ``tests/test_styles/golden`` are the proof.

Scale is the one property that cannot simply be appended: a word given
1.25 in a ``pop`` look should pop *from* 1.25, not have its pop replaced,
and libass has no way to compose a static ``\\fscx`` with a ``\\t`` chain
that was already emitted. So the scale is folded into the transform
instead -- ``scaled_transform_tags`` rebuilds the pop around the word's
own base -- and ``include_scale`` tells ``override_tags`` that the caller
has already dealt with it.

Deliberately absent: font family and outline. Those stay properties of the
look (design section 2); ``WordStyle`` has no field for either.

Imports only ``ass_format`` and ``schema``: ``render_glow`` imports *this*
module (for the pop transform and the metric-changing tags), so this one
must never import it back.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from .ass_format import ass_inline_colour, outline_width
from .render_anim import SCALE_EFFECTS, word_animation_tags
from .schema import Style, WordStyle

# The mapping ``render_ass`` takes: a word's (start, end) -> its override.
# Timings are unique within a transcript and survive card building, so no
# index arithmetic can drift out of alignment (spec Interfaces).
WordStyles = Mapping[tuple[float, float], WordStyle]

# Half the pop "bounce": scale up over the first POP_HALF_MS, back over the
# next. Also re-exported from render_glow, where the halo has always named it.
POP_HALF_MS = 90

# How long a per-word animation runs when the word does not say. The pop's
# own full length, so a word given its own animation keeps the tempo of
# the look it sits in rather than introducing a second one.
WORD_ANIM_DEFAULT_MS = POP_HALF_MS * 2

# Fullwidth lookalikes: visually close to the ASCII originals, structurally
# inert to the ASS/libass tag parser.
_ESCAPE_MAP = {"{": "｛", "}": "｝", "\\": "＼"}


def word_style_for(word, word_styles: WordStyles | None) -> WordStyle | None:
    """This word's override, or ``None``. An override that sets nothing is
    ``None`` too, so an empty ``{}`` from the API renders as the look."""
    if not word_styles:
        return None
    found = word_styles.get((word.start, word.end))
    if found is None or found.is_empty():
        return None
    return found


def scale_pct(ws: WordStyle | None) -> int:
    """The word's own size as an integer percentage of the look's; 100 when
    it has none, which is also the value that keeps the output unchanged."""
    if ws is None or ws.scale is None:
        return 100
    return round(ws.scale * 100)


def scale_transform_tags(scale: float, half_ms: int = POP_HALF_MS) -> str:
    """The ``\\t`` pair the pop effect uses: up to ``scale`` and back to 100."""
    pct = round(scale * 100)
    return f"\\t(0,{half_ms},\\fscx{pct}\\fscy{pct})\\t({half_ms},{2 * half_ms},\\fscx100\\fscy100)"


def scaled_transform_tags(scale: float, base_pct: int, half_ms: int) -> str:
    """The pop ``\\t`` chain rebuilt around a base other than 100%.

    ``scale_transform_tags`` is this with ``base_pct=100``, and stays the
    one used there so nothing without an override changes a byte.
    """
    peak = round(scale * base_pct)
    return (
        f"\\fscx{base_pct}\\fscy{base_pct}"
        f"\\t(0,{half_ms},\\fscx{peak}\\fscy{peak})"
        f"\\t({half_ms},{2 * half_ms},\\fscx{base_pct}\\fscy{base_pct})"
    )


def face_tags(ws: WordStyle | None, *, include_scale: bool = True) -> tuple[str, str]:
    """Weight, slant and (optionally) size: the properties that change the
    word's *metrics*, and so must be applied on every layer that has to
    line up -- including the transparent words of a glow halo."""
    if ws is None:
        return "", ""
    open_parts: list[str] = []
    close_parts: list[str] = []
    if ws.bold is not None:
        # The [V4+ Styles] line is always Bold=0, Italic=0 (ass_format),
        # so \b0 / \i0 is the correct restore for every look.
        open_parts.append("\\b1" if ws.bold else "\\b0")
        close_parts.append("\\b0")
    if ws.italic is not None:
        open_parts.append("\\i1" if ws.italic else "\\i0")
        close_parts.append("\\i0")
    if include_scale and ws.scale is not None:
        pct = scale_pct(ws)
        open_parts.append(f"\\fscx{pct}\\fscy{pct}")
        close_parts.append("\\fscx100\\fscy100")
    return "".join(open_parts), "".join(close_parts)


def override_tags(
    ws: WordStyle | None, *, restore_colour: str | None = None, include_scale: bool = True
) -> tuple[str, str]:
    """The word's own tags, and the tags that undo them.

    ``restore_colour`` is the inline colour the words after this one
    expect; pass ``None`` when the caller's own closing block already
    restores it (the active word in ``line_text``) or when nothing
    follows on the line (``render._box_events``, one word per event).
    """
    if ws is None:
        return "", ""
    open_tags = ""
    close_tags = ""
    if ws.colour is not None:
        open_tags = f"\\c{ass_inline_colour(ws.colour)}"
        if restore_colour is not None:
            close_tags = f"\\c{restore_colour}"
    face_open, face_close = face_tags(ws, include_scale=include_scale)
    return open_tags + face_open, close_tags + face_close


def karaoke_override_tags(ws: WordStyle | None, style: Style) -> tuple[str, str]:
    """As ``override_tags``, for a ``\\kf`` card.

    A karaoke look sweeps a fill from SecondaryColour to PrimaryColour, so
    a per-word colour has to set both (``\\c`` and ``\\2c``) for the word to
    read as that colour rather than only after the sweep reaches it. The
    cost is that the sweep is invisible on that one word -- the rest of the
    line still fills normally -- which is the consistent answer: a word
    given amber is amber in every look family.
    """
    if ws is None:
        return "", ""
    open_tags = ""
    close_tags = ""
    if ws.colour is not None:
        inline = ass_inline_colour(ws.colour)
        open_tags = f"\\c{inline}\\2c{inline}"
        close_tags = (
            f"\\c{ass_inline_colour(style.colors.active)}\\2c{ass_inline_colour(style.colors.text)}"
        )
    face_open, face_close = face_tags(ws)
    return open_tags + face_open, close_tags + face_close


# ---------------------------------------------------------------------------
# the look's own per-word text and inline effect tags
# ---------------------------------------------------------------------------


def escape_ass_text(text: str) -> str:
    return "".join(_ESCAPE_MAP.get(ch, ch) for ch in text)


# The marks that only separate clauses, in the scripts we caption. These
# are what "no_stops" drops; ? and ! are deliberately not here, in any
# script, because they carry tone rather than structure.
_STOP_MARKS = frozenset(
    ".,;:…"          # . , ; : ...
    "。、，；："   # CJK . , , ; :
    "،؛۔"  # Arabic comma, Arabic semicolon, Urdu full stop
)
# Kept inside a word even by "none": stripping these makes the word wrong
# rather than plainer -- "don't" -> "dont", "l'ami" -> "lami",
# "twenty-five" -> "twentyfive". Only counted when a letter sits on both
# sides, so a quoted 'word' or a leading dash still goes.
_INTRA_WORD_MARKS = frozenset("'’-‐‑")


def apply_case(text: str, mode: str) -> str:
    """``str.upper``/``str.lower``, deliberately. Nothing cleverer:
    a German eszett must become SS under ALL CAPS, which is what
    ``str.upper`` already does and what a title-caser would not."""
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    return text


def apply_punctuation(text: str, mode: str) -> str:
    """Drop the marks ``mode`` says to drop, from one word.

    A token that is nothing but punctuation comes back empty. That is
    left alone rather than special-cased: the word list drives the
    active-word index and the karaoke timings, so removing an entry
    would slide every later word onto the wrong moment.
    """
    if mode == "keep" or not text:
        return text
    if mode == "no_stops":
        return "".join(ch for ch in text if ch not in _STOP_MARKS)
    kept = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch)[0] != "P":
            kept.append(ch)
            continue
        if (
            ch in _INTRA_WORD_MARKS
            and 0 < i < len(text) - 1
            and text[i - 1].isalpha()
            and text[i + 1].isalpha()
        ):
            kept.append(ch)
    return "".join(kept)


def prepare_word_text(text: str, style: Style) -> str:
    """The look's treatment of one word, then ASS escaping.

    Punctuation before case, so a mark removed is never a mark that was
    uppercased first -- the two are independent, and doing it in this
    order keeps ``apply_case`` operating on the text that will actually
    be drawn.
    """
    text = apply_punctuation(text, style.punctuation)
    return escape_ass_text(apply_case(text, style.case_mode))


def line_text(
    words: tuple,
    *,
    active_index: int,
    style: Style,
    word_styles: WordStyles | None = None,
    event_ms: int = 0,
    line_scaling: bool = False,
) -> str:
    """Full sentence, active word wrapped with colour + its effect tags.

    Each word already has its own ``{...}`` block, which is the seam the
    per-word overrides of v0.6 section 2 merge into: the look's tags are
    emitted first and the word's last, so the word wins, and a closing
    block puts the line back for the words after it. A word with no
    override renders exactly as before, trailing block and all.
    """
    text_colour = ass_inline_colour(style.colors.text)
    active_colour = ass_inline_colour(style.colors.active)
    parts = []
    for i, word in enumerate(words):
        word_text = prepare_word_text(word.text, style)
        ws = word_style_for(word, word_styles)
        if i == active_index:
            open_tags, close_tags = active_word_tags(
                style, active_colour, text_colour, ws,
                event_ms=event_ms, line_scaling=line_scaling,
            )
        else:
            extra_open, extra_close = override_tags(
                ws, restore_colour=text_colour, include_scale=not line_scaling
            )
            open_tags = f"\\c{text_colour}{extra_open}"
            close_tags = extra_close
        parts.append(f"{{{open_tags}}}{word_text}" + (f"{{{close_tags}}}" if close_tags else ""))
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


def active_word_tags(
    style: Style,
    active_colour: str,
    text_colour: str,
    ws: "WordStyle | None" = None,
    *,
    event_ms: int = 0,
    line_scaling: bool = False,
) -> tuple[str, str]:
    """The active word's own tags.

    ``line_scaling`` says this event already carries a line-level zoom or
    bounce (v0.7). That matters because every ``\\fscx`` written inside the
    line -- the pop's chain, its ``\\fscx100`` close, even a word's own
    static size -- overrides the line-level animation from that point in
    the text onward, so the first word would arrive animated and every
    word after it would snap to full size. Measured: with entrance=zoom
    and active_word=pop, word one scaled 40->100 while words two and three
    never moved. Under ``line_scaling`` the scale is left entirely to the
    entrance and only colour, weight and slant are emitted here.
    """
    effect = style.active_word.effect
    base_pct = scale_pct(ws)
    # v0.7 item 3: a word carrying its own animation runs that instead of
    # the look's motion. It is a replacement, not an addition, because
    # zoom and bounce animate the same \fscx/\fscy the pop does and two
    # chains on one span do not compose -- the second silently wins. The
    # look's colour swap is kept either way, so the word still reads as
    # the active one.
    if (
        ws is not None
        and ws.animation not in (None, "none")
        and not (line_scaling and ws.animation in SCALE_EFFECTS)
    ):
        duration_ms = ws.duration_ms if ws.duration_ms is not None else WORD_ANIM_DEFAULT_MS
        if event_ms > 0:
            duration_ms = min(duration_ms, event_ms)
        anim_open, anim_close = word_animation_tags(
            ws.animation, duration_ms, event_ms,
            base_pct=base_pct, outline=outline_width(style),
        )
        if anim_open:
            # The animation already carries the word's own size, so the
            # override must not emit a second static \fscx after it.
            extra_open, extra_close = override_tags(ws, include_scale=False)
            return (
                f"\\c{active_colour}{anim_open}{extra_open}",
                f"\\c{text_colour}{anim_close}{extra_close}",
            )
    if line_scaling:
        # The entrance owns \fscx for this event; see the docstring.
        extra_open, extra_close = override_tags(ws, include_scale=False)
        return f"\\c{active_colour}{extra_open}", f"\\c{text_colour}{extra_close}"
    # A word given its own size should pop *from* that size, not have the
    # pop replaced, and libass cannot compose a static \fscx with a \t
    # chain already emitted -- so the size is folded into the chain and
    # the override contributes colour, weight and slant only.
    baked_scale = False
    if effect in ("pop", "glow"):
        # glow's visible text is the pop rendering; its halo is a separate
        # layer-0 event built by render_glow.halo_line_text.
        if base_pct == 100:
            transform = scale_transform_tags(style.active_word.scale)
        else:
            transform = scaled_transform_tags(style.active_word.scale, base_pct, POP_HALF_MS)
            baked_scale = True
        open_tags = f"\\c{active_colour}{transform}"
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
    # The active word's own close block already restores the text colour.
    extra_open, extra_close = override_tags(ws, include_scale=not baked_scale)
    return open_tags + extra_open, close_tags + extra_close


_SHAKE_QUARTER_MS = 45


def pop_scale_tags(style: Style, event_ms: int, base_pct: int = 100) -> str:
    """The scale_box bounce, as tags without their surrounding braces so
    a per-word override can share the block. ``base_pct`` is the word's own
    size: the bounce runs from it and back to it, not from 100%."""
    scale = round(style.active_word.scale * base_pct)
    d = min(POP_HALF_MS, max(1, event_ms // 2))
    chain = f"\\t(0,{d},\\fscx{scale}\\fscy{scale})\\t({d},{2 * d},\\fscx{base_pct}\\fscy{base_pct})"
    return chain if base_pct == 100 else f"\\fscx{base_pct}\\fscy{base_pct}{chain}"
