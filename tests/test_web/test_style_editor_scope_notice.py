"""v0.6 track D, task 1: the Styles page must say what Save actually does.

A saved style is written to the user styles directory and layered over
the shipped one *by name* (`styles/library.py`'s `list_styles`), and a job
stores only the style's name, never its content (`pipeline/db.py`). So
editing a built-in look changes every job that uses it, including old
jobs the moment they're restyled or re-burned -- and that must be visible
next to Save whenever a shipped look is open, not only once it already
carries a local override (the old behaviour: `resetBtn.hidden = !style.shipped`
paired with a warning gated on `customized_locally` alone).

No headless browser in this suite (see test_pages.py's convention) --
these assert on the served static markup/source text directly."""
from __future__ import annotations

from ash_captions.web.app import STATIC_DIR


def _html() -> str:
    return (STATIC_DIR / "style_editor.html").read_text(encoding="utf-8")


def _js() -> str:
    return (STATIC_DIR / "style_editor.js").read_text(encoding="utf-8")


def test_scope_notice_markup_exists():
    html = _html()
    assert 'id="scope-notice"' in html
    assert 'id="scope-notice-text"' in html
    assert 'id="scope-reset-btn"' in html
    # Save-as already sits beside Save as the non-destructive path.
    assert 'id="save-as-btn"' in html


def test_scope_notice_is_shown_for_every_shipped_look_not_only_customized_ones():
    js = _js()
    # The gate must be `style.shipped`, not `style.customized_locally` --
    # the bug this task fixes was warning only after an override already
    # existed, when the danger is in the *first* save.
    assert "if (!style.shipped)" in js
    assert "scopeResetBtn.hidden = !style.customized_locally" in js


def test_scope_notice_names_the_look_and_says_what_save_does():
    js = _js()
    assert "Saving changes" in js
    assert "for every job that uses it on this PC" in js
    assert "restyled or burned again" in js
    assert "Files already produced keep the captions they have" in js
