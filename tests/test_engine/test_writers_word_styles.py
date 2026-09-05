"""``engine.writers`` must forward ``word_styles`` to the real renderer.

This is a shim, and the shim was the hole. ``app/adapter.py`` and
``app/runner.py`` call ``engine.write_ass``, not ``styles.render.write_ass``;
the runner goes through ``_call_with_optionals``, which *drops* a keyword the
callee does not accept. So a shim missing the parameter does not raise -- the
burn just quietly produces captions with none of the editor's per-word
colours in them, and every test of the renderer itself still passes.

That is the shape of every serious bug this project has had. These tests
fail loudly if the keyword stops being forwarded.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.engine.writers import render_ass, write_ass
from ash_captions.styles.schema import Style, WordStyle

WORDS = (
    Word(text="hello", start=0.0, end=0.3),
    Word(text="there", start=0.3, end=0.6),
    Word(text="world", start=0.6, end=1.0),
)
CARD = Card(words=WORDS, start=0.0, end=1.0)
THERE = (0.3, 0.6)
AMBER = WordStyle(colour="#FFD166")
AMBER_INLINE = "\\c&H66D1FF&"  # #FFD166 as ASS's &HBBGGRR&


def look() -> Style:
    return Style.from_dict(
        {"name": "T", "colors": {"text": "#FFFFFF", "active": "#00E28A"},
         "active_word": {"effect": "pop", "scale": 1.12}},
        check_font=False,
    )


def test_both_shim_functions_accept_the_keyword():
    """A missing parameter here is invisible at runtime: the runner drops
    unknown keywords rather than raising."""
    for fn in (render_ass, write_ass):
        assert "word_styles" in inspect.signature(fn).parameters, fn.__name__


def test_a_per_word_colour_reaches_the_rendered_ass_through_the_shim():
    out = render_ass([CARD], look(), word_styles={THERE: AMBER})
    assert AMBER_INLINE in out


def test_the_colour_lands_on_that_word_and_not_its_neighbours():
    out = render_ass([CARD], look(), word_styles={THERE: AMBER})
    for line in out.splitlines():
        if not line.startswith("Dialogue:") or AMBER_INLINE not in line:
            continue
        before, _, after = line.partition(AMBER_INLINE)
        # The override opens immediately before its own word.
        assert after.lstrip("}").startswith("there"), line


def test_no_word_styles_renders_exactly_what_it_rendered_before():
    plain = render_ass([CARD], look())
    assert render_ass([CARD], look(), word_styles=None) == plain
    assert render_ass([CARD], look(), word_styles={}) == plain
    assert render_ass([CARD], look(), word_styles={(9.0, 9.5): AMBER}) == plain


def test_write_ass_puts_the_override_on_disk(tmp_path: Path):
    path = write_ass([CARD], tmp_path / "out.ass", look(), word_styles={THERE: AMBER})
    assert AMBER_INLINE in path.read_text(encoding="utf-8")
