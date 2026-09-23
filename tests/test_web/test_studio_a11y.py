"""What the Studio and the Queue promise the keyboard and the screen reader.

Source-level guards for the 2026-09-23 critique's keyboard and ARIA
findings. Each asserts the shape the page ships, in the style of the
guide and Styles-page tests; the behaviour itself was driven in a browser
when the change was made.
"""
from __future__ import annotations

from pathlib import Path

import ash_captions.web as web

STATIC = Path(web.__file__).parent / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class TestSpaceBelongsToTheFocusedControl:
    def test_the_players_space_handler_leaves_buttons_alone(self):
        """Space on a focused Burn used to play the video instead."""
        source = _static("studio.js")
        assert '"BUTTON", "SUMMARY"' in source


class TestTheTranscriptIsOneTabStop:
    def test_words_rove_rather_than_each_being_a_stop(self):
        source = _static("studio_edit.js")
        assert "function rove(" in source
        assert 'querySelector(".tw-text").tabIndex = -1' in source

    def test_the_arrows_move_between_words_and_brackets_reach_the_edges(self):
        source = _static("studio_edit.js")
        for key in ('"ArrowRight"', '"ArrowLeft"', '"Home"', '"End"', 'e.key === "["'):
            assert key in source, key

    def test_a_grip_is_a_slider_the_keyboard_can_nudge(self):
        source = _static("studio_edit.js")
        assert 'grip.setAttribute("role", "slider")' in source
        assert "aria-valuenow" in source and "aria-valuetext" in source
        assert "function gripKey(" in source
        assert "nudgeRetime(state.words, index, edge, delta)" in source

    def test_the_hint_tells_the_editor_the_keys_exist(self):
        source = _static("studio_edit.js")
        assert "press [ or ]" in source

    def test_a_focused_grip_is_visible(self):
        assert ".tw-grip:focus-visible" in _static("studio_edit.css")


class TestOneSummaryNotAWarningPerLine:
    def test_the_panel_renders_one_summary_and_marks_the_lines(self):
        source = _static("studio_edit.js")
        assert "lineSummary(state.lines, state.maxWords, state.lookName)" in source
        assert 'row.classList.add("is-split")' in source
        assert 'warn.className = "tedit-warn"' not in source

    def test_the_summary_and_the_mark_are_styled(self):
        css = _static("studio_edit.css")
        assert ".tedit-summary" in css
        assert ".tedit-line.is-split" in css


class TestTheLooksListIsAValidListbox:
    def test_groups_are_groups_and_headings_are_hidden_from_the_tree(self):
        source = _static("studio_looks.js")
        assert 'section.setAttribute("role", "group")' in source
        assert 'h3.setAttribute("aria-hidden", "true")' in source

    def test_an_option_is_named_by_its_look_not_its_sample_text(self):
        assert 'el.setAttribute("aria-label", style.name)' in _static("studio_looks.js")


class TestTabsSayWhy:
    def test_a_disabled_tab_carries_its_reason(self):
        source = _static("studio_panes.js")
        assert "button.title = ok ? \"\" : tab.reason" in source
        assert "9:16 reel has framing to correct" in source

    def test_the_check_tab_is_named_with_its_count(self):
        assert "uncertain word" in _static("studio_panes.js")


class TestTheQueueStopsChattering:
    def test_the_status_line_only_changes_when_its_text_does(self):
        source = _static("app.js")
        assert "if (queueHealth.textContent !== text) queueHealth.textContent = text;" in source
        assert "Math.floor(age / 10000) * 10000" in source

    def test_the_job_list_is_not_a_live_region(self):
        """The finished-job toast is already role=status; a list whose
        progress text changed every second was read out in full."""
        html = _static("index.html")
        assert '<div id="job-list"></div>' in html
        assert 'aria-live' not in html.split('id="job-list"')[1][:80]
        assert 'setAttribute("role", "status")' in _static("toast.js")
