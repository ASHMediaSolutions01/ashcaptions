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


def test_finished_queue_rows_show_one_primary_action_and_no_bar():
    """Spec v0.5 section 5: one primary button per row (Open in Studio; Retry
    on a failed row), the housekeeping as subtle buttons, and a finished job
    shows its pill and time rather than a full green bar."""
    from ash_captions.web.app import STATIC_DIR

    queue_js = (STATIC_DIR / "queue.js").read_text(encoding="utf-8")
    actions = queue_js[queue_js.index("function actionsFor") : queue_js.index("// ---- actions ----")]
    assert actions.count('"btn small primary"') == 1  # Open in Studio
    assert 'button("Retry", "primary"' in actions
    assert 'button("Open folder", "subtle"' in actions
    assert 'button("Copy path", "subtle"' in actions
    assert 'button("Remove", "quiet"' in actions
    assert 'refs.track.hidden = finished' in queue_js
    assert 'stageText = "Done"' not in queue_js  # the badge already says Done


def test_disabled_primary_button_stays_readable():
    from ash_captions.web.app import STATIC_DIR

    theme = (STATIC_DIR / "theme.css").read_text(encoding="utf-8")
    assert ".btn.primary:disabled" in theme
    assert ".btn.subtle {" in theme
    # The old rule faded every disabled button to 45%, which made the filled
    # Start button an unreadable purple ghost. (The nav's disabled Studio
    # link keeps its own 0.45; that one is a link, not a button.)
    assert '.btn:disabled, .btn[aria-disabled="true"] { opacity: 0.45;' not in theme


def test_studio_video_gets_the_job_thumbnail_as_poster(client, app):
    """Spec v0.5 section 5: the video shows the job thumbnail as its poster so
    a clip that opens on white does not look broken. The page is one static
    file for every job, so studio_poster.js sets it from the URL before
    studio.js loads the video."""
    from ash_captions.web.app import STATIC_DIR

    page = client.get("/studio/any-id").text
    poster_tag = page.index(f"/static/studio_poster.js?v={app.state.version}")
    assert poster_tag < page.index(f"/static/studio.js?v={app.state.version}")
    script = (STATIC_DIR / "studio_poster.js").read_text(encoding="utf-8")
    assert "video.poster = `/api/jobs/${encodeURIComponent(jobId)}/thumb`" in script
    assert 'poster="' not in (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
