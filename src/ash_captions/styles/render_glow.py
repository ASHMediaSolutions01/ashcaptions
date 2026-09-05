"""The halo layer of the ``glow`` active-word effect (design 2026-09-04,
section 3).

Before v0.5 a glowing word was one Dialogue line: the active word's fill in
the active colour *and* a widened, blurred outline in the same colour, so
fill and halo merged into one blob and the letterforms vanished. Now a glow
card is two Dialogue lines per word event:

  * layer ``HALO_LAYER`` (0), built here -- the same text with every other
    word fully transparent (``\\alpha&HFF&``), the active word with a
    transparent fill (``\\1a&HFF&``) and shadow (``\\4a&HFF&``), an opaque
    widened outline in the active colour (``\\3a&H00&\\3c..\\bord..``) and
    ``\\blur4\\be1`` to soften it into a halo;
  * layer ``TEXT_LAYER`` (1), built by ``render._line_text`` -- exactly what
    the ``pop`` effect emits: active-colour fill, the style's own outline,
    no blur.

libass draws lower layers first, so the halo sits behind a crisp word with
its dark outline intact. Both layers carry the same pop scale transform so
the halo grows with the word, and the caller prepends the same leading
override block (``\\fad``/``\\move``) to both so they enter and leave
together.

Hidden words still occupy their width on the halo line, which is what keeps
the halo centred under the same word on both layers. Only ``ass_format`` and
``schema`` are imported here so ``render`` can import this module without a
cycle.
"""
from __future__ import annotations

from collections.abc import Sequence

from .ass_format import ass_inline_colour, outline_width

# POP_HALF_MS and scale_transform_tags are the pop bounce, shared with the
# text layer; they live in render_word (which this module builds on) and are
# re-exported here because the halo and its tests have always named them.
from .render_word import POP_HALF_MS, face_tags, scale_pct, scale_transform_tags, scaled_transform_tags
from .schema import Style, WordStyle

HALO_LAYER = 0
TEXT_LAYER = 1

_HIDDEN = "\\alpha&HFF&"
_SOFTEN = "\\blur4\\be1"


def glow_width(style: Style) -> int:
    """The widened ``\\bord`` behind a glowing word: roughly double the base
    outline, and never less than 3px wider, so the blur reads as a halo
    rather than a slightly thicker stroke."""
    base = outline_width(style)
    return max(base + 3, base * 2)


def halo_line_text(
    prepared_words: Sequence[str],
    active_index: int,
    style: Style,
    overrides: Sequence["WordStyle | None"] | None = None,
) -> str:
    """The layer-0 Text field for one word event. ``prepared_words`` are
    already escaped/uppercased (``render._prepare_word_text``); the leading
    override block is *not* included -- the caller adds the same one to
    both layers.

    ``overrides`` is one ``WordStyle`` (or ``None``) per word, aligned with
    ``prepared_words`` -- the per-word styling of v0.6 section 2. The
    active word's colour override becomes the halo's own colour (``\\3c``);
    weight, slant and size are applied to **every** word, hidden ones
    included, because the hidden words are what hold the halo's text
    metrics equal to the text layer's. Scale it one word on layer 1 but not
    on layer 0 and every halo after it slides off its word. ``None``
    renders byte-identically to the renderer before this feature.
    """
    active_colour = ass_inline_colour(style.colors.active)
    parts: list[str] = []
    for i, word_text in enumerate(prepared_words):
        ws = overrides[i] if overrides is not None and i < len(overrides) else None
        if i == active_index:
            halo_colour = ass_inline_colour(ws.colour) if ws is not None and ws.colour is not None else active_colour
            base_pct = scale_pct(ws)
            transform = (
                scale_transform_tags(style.active_word.scale)
                if base_pct == 100
                else scaled_transform_tags(style.active_word.scale, base_pct, POP_HALF_MS)
            )
            # The scale is already folded into the transform, so the word's
            # own tags contribute weight and slant only.
            face_open, face_close = face_tags(ws, include_scale=False)
            open_tags = (
                f"\\1a&HFF&\\3a&H00&\\4a&HFF&\\3c{halo_colour}\\bord{glow_width(style)}{_SOFTEN}"
                f"{transform}{face_open}"
            )
            close_tags = f"{_HIDDEN}\\bord{outline_width(style)}\\blur0\\be0\\fscx100\\fscy100{face_close}"
            parts.append(f"{{{open_tags}}}{word_text}{{{close_tags}}}")
        else:
            face_open, face_close = face_tags(ws)
            if face_open:
                parts.append(f"{{{_HIDDEN}{face_open}}}{word_text}{{{face_close}}}")
            else:
                parts.append(f"{{{_HIDDEN}}}{word_text}")
    return " ".join(parts)
