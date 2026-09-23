"""The failed-job experience on the Queue card and in the Studio.

Source-level guards, in the style of the guide and Styles-page tests: the
behaviour is in the browser, but what the browser needs from the page can
be read off the files it is served.
"""
from __future__ import annotations

from pathlib import Path

import ash_captions.web as web

STATIC = Path(web.__file__).parent / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class TestTheQueueCard:
    def test_the_card_shows_the_reason_and_folds_the_technical_text(self):
        source = _static("queue.js")
        assert "job.reason" in source
        assert "Technical details" in source
        assert "job-technical" in source

    def test_retry_is_only_the_primary_action_when_it_can_help(self):
        source = _static("queue.js")
        # `!== false` on purpose: a queue that predates the field still gets Retry.
        assert "job.retryable !== false" in source
        # Retry leads when it can help; otherwise the demoted "Try again".
        assert 'canHelp ? "primary" : "subtle"' in source
        assert 'canHelp ? "Retry" : "Try again"' in source

    def test_the_technical_block_is_styled_as_data_not_as_the_message(self):
        css = _static("style.css")
        assert ".job-technical" in css
        assert ".job-reason" in css


class TestTheFailedStudio:
    def test_the_studio_uses_the_reason_and_hides_what_cannot_work(self):
        source = _static("studio.js")
        assert "job.reason" in source
        assert "function failedState" in source
        # A failed job has no captions to restyle, burn, or export.
        for element_id in ('"looks"', '"pane-tabs"', '"export"'):
            assert element_id in source, element_id

    def test_the_page_carries_the_elements_the_failed_state_hides(self):
        html = _static("studio.html")
        for element_id in ("looks", "pane-tabs", "export", "burn-btn", "reframe-btn"):
            assert f'id="{element_id}"' in html, element_id
