"""The two HTML pages are served with the running version stamped into
their asset URLs (web/app.py `render_page`) -- a shipped update must never
pair a browser-cached app.js with a new index.html."""
from __future__ import annotations

import re


def test_index_stamps_the_running_version_into_asset_urls(client, app):
    res = client.get("/")
    assert res.status_code == 200
    assert "__VERSION__" not in res.text
    version = app.state.version
    assert f"/static/app.js?v={version}" in res.text
    assert f"/static/style.css?v={version}" in res.text
    assert f"/static/api.js?v={version}" in res.text


def test_style_editor_stamps_the_running_version_too(client, app):
    res = client.get("/style-editor")
    assert res.status_code == 200
    assert "__VERSION__" not in res.text
    assert f"/static/style_editor.js?v={app.state.version}" in res.text


def test_no_hand_typed_version_survives_in_the_sources():
    from ash_captions.web.app import STATIC_DIR

    for name in ("index.html", "style_editor.html"):
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"\?v=\d", html), f"{name} has a hand-typed version"
        assert "__VERSION__" in html


def test_pages_carry_the_form_and_accessibility_hooks():
    from ash_captions.web.app import STATIC_DIR

    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '<form class="card submit-card" id="submit-form"' in index
    assert 'id="start-btn"' in index and 'type="submit"' in index
    assert 'id="browse-btn"' in index  # the native picker (POST /api/pick-file)
    assert 'id="options" disabled' in index  # options shown always, off until a file is chosen
    assert 'id="dropzone" tabindex="0" role="button"' in index
    assert 'id="job-list" aria-live="polite"' in index
    assert 'id="clear-finished-btn"' in index
    assert 'id="start-here"' in index
    assert "alert(" not in (STATIC_DIR / "queue.js").read_text(encoding="utf-8")
    assert "window.prompt" not in (STATIC_DIR / "style_editor.js").read_text(encoding="utf-8")


def test_theme_respects_reduced_motion_and_has_one_focus_ring():
    from ash_captions.web.app import STATIC_DIR

    theme = (STATIC_DIR / "theme.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in theme
    assert ":focus-visible { box-shadow: var(--focus)" in theme
    assert ".badge::before" in theme  # a dot next to the word, never colour alone
