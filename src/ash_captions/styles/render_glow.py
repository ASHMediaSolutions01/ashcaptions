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
from .schema import Style

HALO_LAYER = 0
TEXT_LAYER = 1

# Half the pop "bounce": scale up over the first POP_HALF_MS, back over the next.
POP_HALF_MS = 90

_HIDDEN = "\\alpha&HFF&"
_SOFTEN = "\\blur4\\be1"


def scale_transform_tags(scale: float, half_ms: int = POP_HALF_MS) -> str:
    """The ``\\t`` pair the pop effect uses: up to ``scale`` and back to 100."""
    pct = round(scale * 100)
    return f"\\t(0,{half_ms},\\fscx{pct}\\fscy{pct})\\t({half_ms},{2 * half_ms},\\fscx100\\fscy100)"


def glow_width(style: Style) -> int:
    """The widened ``\\bord`` behind a glowing word: roughly double the base
    outline, and never less than 3px wider, so the blur reads as a halo
    rather than a slightly thicker stroke."""
    base = outline_width(style)
    return max(base + 3, base * 2)


def halo_line_text(prepared_words: Sequence[str], active_index: int, style: Style) -> str:
    """The layer-0 Text field for one word event. ``prepared_words`` are
    already escaped/uppercased (``render._prepare_word_text``); the leading
    override block is *not* included -- the caller adds the same one to
    both layers."""
    active_colour = ass_inline_colour(style.colors.active)
    open_tags = (
        f"\\1a&HFF&\\3a&H00&\\4a&HFF&\\3c{active_colour}\\bord{glow_width(style)}{_SOFTEN}"
        f"{scale_transform_tags(style.active_word.scale)}"
    )
    close_tags = f"{_HIDDEN}\\bord{outline_width(style)}\\blur0\\be0\\fscx100\\fscy100"
    parts: list[str] = []
    for i, word_text in enumerate(prepared_words):
        if i == active_index:
            parts.append(f"{{{open_tags}}}{word_text}{{{close_tags}}}")
        else:
            parts.append(f"{{{_HIDDEN}}}{word_text}")
    return " ".join(parts)
