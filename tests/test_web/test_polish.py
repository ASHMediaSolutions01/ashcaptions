"""The polish pass of 2026-09-23: contrast, the neutral-chrome promise,
stale targets, and copy. Where a rule can be computed it is computed
(the hover contrast), not string-matched.
"""
from __future__ import annotations

import re
from pathlib import Path

import ash_captions.web as web

STATIC = Path(web.__file__).parent / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _token(css: str, name: str) -> str:
    match = re.search(r"--%s:\s*(#[0-9a-fA-F]{6})" % re.escape(name), css)
    assert match, name
    return match.group(1)


def _luminance(hex_colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class TestContrast:
    def test_white_text_on_the_primary_button_holds_in_every_state(self):
        theme = _static("theme.css")
        for token in ("accent-fill", "accent-fill-hover"):
            assert contrast("#ffffff", _token(theme, token)) >= 4.5, token


class TestNeutralChrome:
    def test_nothing_in_the_chrome_carries_the_old_blue_lift(self):
        """theme.css promises R = G = B on every grey; the look slab was the
        one place that broke it, on the element editors stare at longest."""
        for name in ("studio.css", "look_card.css", "theme.css", "style.css", "style_editor.css"):
            # Values only: theme.css names #1c1f26 in the comment that explains why it went.
            assert ": #1a1d24" not in _static(name), name
            assert ": #1c1f26" not in _static(name), name

    def test_the_look_sample_sits_on_the_same_mat_as_the_video(self):
        assert "background: var(--mat)" in _static("studio.css")
        assert "var(--mat" in _static("look_card.css")

    def test_a_long_look_name_wraps_rather_than_truncating(self):
        # Both lists: the Studio's looks column and the Styles page's own list.
        for name, selector in (("studio.css", ".look-name"), ("style_editor.css", ".style-item-name")):
            rule = _static(name).split(selector + " {")[1][:140]
            assert "overflow-wrap: anywhere" in rule, name
            assert "text-overflow: ellipsis" not in rule, name


class TestNoLayoutTransitions:
    def test_width_is_not_animated_anywhere(self):
        for name in ("theme.css", "guide.css", "style.css", "studio.css"):
            assert "transition: width" not in _static(name), name


class TestTheGuideReads:
    def test_callouts_have_no_coloured_side_bar(self):
        css = _static("guide.css")
        note = css.split(".note {")[1].split("}")[0]
        assert "border-left" not in note
        assert "border: 1px solid" in note

    def test_the_measure_is_within_the_reading_floor(self):
        match = re.search(r"\.guide-main \{ max-width: (\d+)ch; \}", _static("guide.css"))
        assert match and int(match.group(1)) <= 75

    def test_the_title_has_reading_leading(self):
        assert ".guide-header h1 { line-height: 1.3; }" in _static("guide.css")


class TestStaleTargets:
    def test_the_nav_checks_the_remembered_job_is_still_done(self):
        source = _static("nav.js")
        assert 'j.status === "done"' in source
        assert "String(j.id) === String(remembered)" in source

    def test_the_preview_picker_offers_only_finished_jobs(self):
        assert 'if (job.status !== "done") continue;' in _static("style_editor_preview.js")

    def test_the_preview_picker_skips_upload_copies_that_no_longer_exist(self):
        """A copy under web_uploads is deleted when its job succeeds; offering
        it previews nothing and it collided by name with the real file."""
        assert "web_uploads" in _static("style_editor_preview.js")


class TestCopy:
    def test_the_save_notice_is_a_sentence(self):
        source = _static("style_editor.js")
        assert 'Saving "${style.name}" changes every job on this PC that uses it' in source
        assert 'Saving changes "${style.name}" for every job' not in source

    def test_show_english_is_not_offered_on_an_english_job(self):
        source = _static("studio_check.js")
        assert 'state.language !== "en"' in source
