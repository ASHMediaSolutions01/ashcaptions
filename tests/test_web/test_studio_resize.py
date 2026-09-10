"""The Studio's draggable columns (studio_resize.js).

The complaint this answers: "the studio was getting cramped and I
couldn't change the size of transcript and fixes". The widths were fixed
by media query, so the balance between picture and words was ours to
guess rather than the editor's to set.

``widthsFor`` is the whole policy -- what a drag is allowed to produce --
so it is tested directly under Node. The dragging itself was driven with
a real mouse in a real browser; what is guarded here is the arithmetic
that keeps a drag from producing a layout with no video in it.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ash_captions.web.app import STATIC_DIR

SCRIPT = STATIC_DIR / "studio_resize.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def widths(which: str, proposed: float, edit: float, looks: float, total: float) -> dict:
    """Call the real widthsFor out of the shipped file."""
    driver = (
        f"const m = require({json.dumps(str(SCRIPT))});"
        f"process.stdout.write(JSON.stringify(m.widthsFor("
        f"{json.dumps(which)}, {proposed}, {{edit: {edit}, looks: {looks}}}, {total})));"
    )
    out = subprocess.run(["node", "-e", driver], capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def limits() -> dict:
    driver = (
        f"const m = require({json.dumps(str(SCRIPT))});"
        "process.stdout.write(JSON.stringify({edit: m.MIN_EDIT, looks: m.MIN_LOOKS,"
        " stage: m.MIN_STAGE, collapse: m.COLLAPSE_AT}));"
    )
    out = subprocess.run(["node", "-e", driver], capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestWidthsFor:
    def test_a_plain_drag_moves_the_column_it_is_given(self):
        assert widths("edit", 600, 480, 280, 1440)["edit"] == 600
        assert widths("looks", 340, 480, 280, 1440)["looks"] == 340

    def test_dragging_one_column_never_moves_the_other(self):
        out = widths("edit", 600, 480, 280, 1440)
        assert out["looks"] == 280

    def test_the_video_keeps_a_floor_however_hard_you_drag(self):
        """The bug this prevents is the one the three-column layout was
        built to fix: a panel taking the stage down to a black line."""
        total, lim = 1440, limits()
        out = widths("edit", 5000, 480, 280, total)
        stage_left = total - out["edit"] - out["looks"]
        assert stage_left >= lim["stage"], (out, stage_left)

    def test_the_floor_is_taken_off_the_column_being_dragged(self):
        """Not off the other one: a drag on the words must never quietly
        shrink the looks the editor is comparing."""
        out = widths("edit", 5000, 480, 280, 1440)
        assert out["looks"] == 280

    def test_a_column_cannot_be_dragged_below_its_own_minimum(self):
        lim = limits()
        assert widths("edit", 10, 480, 280, 1440)["edit"] == lim["edit"]
        # Between the collapse point and the minimum: it snaps up to the
        # minimum rather than rendering half a look card.
        between = (lim["collapse"] + lim["looks"]) // 2
        assert widths("looks", between, 480, 280, 1440)["looks"] == lim["looks"]

    def test_the_looks_collapse_rather_than_becoming_a_sliver(self):
        """Below the collapse point it closes fully. A 40px column of
        half-drawn look cards is worse than no column."""
        lim = limits()
        out = widths("looks", lim["collapse"] - 1, 480, 280, 1440)
        assert out["looks"] == 0

    def test_only_the_looks_collapse_never_the_words(self):
        lim = limits()
        assert widths("edit", 1, 480, 280, 1440)["edit"] == lim["edit"]

    def test_a_width_saved_on_a_big_screen_is_re_clamped_on_a_small_one(self):
        """A 900px words column saved on a 1920 monitor must not leave a
        1024 laptop with no video."""
        lim = limits()
        out = widths("edit", 900, 900, 300, 1024)
        assert 1024 - out["edit"] - out["looks"] >= lim["stage"]


def test_the_studio_page_carries_both_handles_and_loads_the_script(client, app):
    page = client.get("/studio/5").text
    assert page.count('class="splitter"') == 2, "one handle either side of the words column"
    assert 'data-splitter="edit"' in page
    assert 'data-splitter="looks"' in page
    assert f"/static/studio_resize.js?v={app.state.version}" in page
    assert client.get("/static/studio_resize.js").status_code == 200


def test_the_handles_are_reachable_by_keyboard_and_named():
    source = SCRIPT.read_text(encoding="utf-8")
    page = (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
    # A <button> is focusable by default; role=separator tells a screen
    # reader what it is.
    assert '<button type="button" class="splitter"' in page
    assert 'role="separator"' in page
    assert "ArrowLeft" in source and "ArrowRight" in source
    assert "aria-valuenow" in source


def test_a_collapsed_looks_column_keeps_its_handle():
    """Hiding the handle with the column made a one-way door: there was
    no way to bring the looks back."""
    css = (STATIC_DIR / "studio.css").read_text(encoding="utf-8")
    assert ".workspace.looks-collapsed > .splitter[data-splitter=\"looks\"] {\n  background" in css
    assert "display: none" not in css.split(".workspace.looks-collapsed > .splitter")[1][:200]
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Click to bring the looks back" in source


def test_a_drag_does_not_also_fire_the_reopening_click():
    """pointerup on a handle is followed by a click. Without the guard,
    collapsing the looks reopened them in the same gesture."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "suppressClick" in source
