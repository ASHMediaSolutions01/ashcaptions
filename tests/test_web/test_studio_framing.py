"""The Studio's reel-framing panel (static/studio_framing.js + .css).

Its pure helpers decide what an editor is shown and what gets sent back,
run under node so the logic is exercised rather than string-matched. The
numbers below are the measured two-shot: the interviewer at x=373 and the
guest at x=1540 in a 1920-wide frame.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ash_captions.web.app import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to run studio_framing.js's helpers")
SCRIPT = STATIC_DIR / "studio_framing.js"

LEFT, RIGHT = 373.0, 1540.0


def run_js(expression: str):
    code = (
        f"const f = require({json.dumps(str(SCRIPT))});"
        f" process.stdout.write(JSON.stringify({expression}));"
    )
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


PLAN = {
    "source_width": 1920,
    "windows": [
        {"start": 0.0, "end": 10.4, "centre": 952.0, "chosen": 0, "candidates": [952.0]},
        {"start": 10.4, "end": 24.7, "centre": RIGHT, "chosen": 1, "candidates": [LEFT, RIGHT]},
        {"start": 24.7, "end": 32.4, "centre": 960.0, "chosen": None, "candidates": []},
        {"start": 32.4, "end": 40.0, "centre": RIGHT, "chosen": 1, "candidates": [LEFT, RIGHT]},
    ],
}


@needs_node
class TestWhatIsShown:
    def test_only_shots_with_a_choice_are_listed(self):
        """A one-person shot has nothing to choose between, and a
        thirty-six row list would bury the handful that matter."""
        rows = run_js(f"f.contestedWindows({json.dumps(PLAN)})")
        assert [r["index"] for r in rows] == [1, 3]

    def test_a_row_keeps_its_index_in_the_real_plan(self):
        """The override names a window in the saved plan, not a row on
        screen -- off by two here, because two windows were filtered out."""
        rows = run_js(f"f.contestedWindows({json.dumps(PLAN)})")
        assert rows[1]["index"] == 3

    def test_a_plan_with_nothing_contested_lists_nothing(self):
        plan = {"source_width": 1920, "windows": [PLAN["windows"][0]]}
        assert run_js(f"f.contestedWindows({json.dumps(plan)})") == []

    def test_rubbish_in_place_of_a_plan_does_not_throw(self):
        assert run_js("f.contestedWindows(null)") == []
        assert run_js("f.contestedWindows({})") == []


@needs_node
class TestLabels:
    def test_two_people_at_opposite_edges_read_as_left_and_right(self):
        assert run_js(f"f.labelsFor([{LEFT}, {RIGHT}], 1920)") == ["left", "right"]

    def test_the_middle_third_is_named(self):
        assert run_js("f.zoneOf(960, 1920)") == "middle"

    def test_two_people_in_the_same_third_fall_back_to_counting(self):
        """Calling both of them "left" would be worse than useless."""
        assert run_js("f.labelsFor([300, 380], 1920)") == [
            "1st from the left", "2nd from the left",
        ]

    def test_an_unknown_frame_width_does_not_produce_empty_labels(self):
        assert run_js("f.labelsFor([300, 380], 0)") == [
            "1st from the left", "2nd from the left",
        ]


@needs_node
class TestWhatIsSent:
    def test_only_changed_choices_are_sent(self):
        """Re-sending the default makes a re-burn look like a change."""
        rows = run_js(f"f.contestedWindows({json.dumps(PLAN)})")
        picks = {str(rows[0]["index"]): 1}  # same as chosen
        assert run_js(
            f"f.changedOverrides({json.dumps(rows)}, {json.dumps(picks)})"
        ) == {}

    def test_a_real_change_is_sent_under_the_plans_own_index(self):
        rows = run_js(f"f.contestedWindows({json.dumps(PLAN)})")
        picks = {"3": 0}
        assert run_js(
            f"f.changedOverrides({json.dumps(rows)}, {json.dumps(picks)})"
        ) == {"3": 0}

    def test_untouched_rows_send_nothing(self):
        rows = run_js(f"f.contestedWindows({json.dumps(PLAN)})")
        assert run_js(f"f.changedOverrides({json.dumps(rows)}, {{}})") == {}


@needs_node
class TestClock:
    def test_seconds_read_as_minutes_and_seconds(self):
        assert run_js("f.clockTime(0)") == "0:00"
        assert run_js("f.clockTime(9.4)") == "0:09"
        assert run_js("f.clockTime(75)") == "1:15"
        assert run_js("f.clockTime(605)") == "10:05"

    def test_a_negative_time_does_not_render_a_minus(self):
        assert run_js("f.clockTime(-3)") == "0:00"


class TestItIsWiredUp:
    def test_the_page_loads_the_panel_and_its_styles(self):
        page = (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
        assert "studio_framing.js" in page
        assert "studio_framing.css" in page
        assert 'id="framing"' in page

    def test_the_pane_is_hidden_until_a_reel_has_been_burned(self):
        page = (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
        framing = page.split('id="framing"')[1].split(">")[0]
        assert "hidden" in framing

    def test_studio_js_mounts_it(self):
        assert "AshStudioFraming" in (STATIC_DIR / "studio.js").read_text(encoding="utf-8")

    def test_each_pane_hides_the_others(self):
        """A pane left visible under another one is how the Studio grew a
        scrollbar over content nobody asked for."""
        css = (STATIC_DIR / "studio.css").read_text(encoding="utf-8")
        for rule in (
            '.panes[data-pane="words"] > .framing',
            '.panes[data-pane="check"] > .framing',
            '.panes[data-pane="framing"] > #transcript-edit',
            '.panes[data-pane="framing"] > .check',
        ):
            assert rule in css, rule


class TestTheEndpointIsQuiet:
    """The Studio asks for the framing on every visit.

    A 404 for the ordinary case -- a job nobody has burned as a reel --
    put a red line in the browser console of every normal Studio page,
    which is exactly the noise that hides a real error. It answers
    instead, and `available` keeps "never a reel" apart from "a reel that
    framed nobody".
    """

    def test_a_job_with_no_reel_answers_rather_than_erroring(self):
        from ash_captions.web.routes_studio import _reframe_plan

        answer = _reframe_plan(Path("nope-not-a-dir"), "5")
        assert answer["available"] is False
        assert answer["windows"] == []

    def test_a_saved_plan_is_returned_and_marked_available(self, tmp_path):
        from ash_captions.engine.reframe import CropPlan, CropWindow
        from ash_captions.engine.reframe_store import save_plan
        from ash_captions.web.routes_studio import _reframe_plan

        burned = tmp_path / "clip.captioned.mp4"
        burned.write_bytes(b"not really a video")
        save_plan(
            CropPlan(1920, 1080, 606, 1080, (CropWindow(0.0, 5.0, RIGHT, 1, (LEFT, RIGHT)),)),
            burned,
        )
        answer = _reframe_plan(tmp_path, "5")
        assert answer["available"] is True
        assert answer["source_width"] == 1920
        assert answer["windows"][0]["candidates"] == [LEFT, RIGHT]
