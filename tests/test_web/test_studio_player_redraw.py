"""Swapping the caption track repaints a paused video.

The renderer only draws on a video frame, so on a paused video the old
captions stayed on screen after a restyle: an editor who paused, then
clicked through looks, saw nothing change until they pressed play. Found
by clicking looks in the real Studio with the video paused (2026-09-04).
`setTrack` now asks the renderer to draw the current moment again.

Run under node, like the caption-check helpers, so the behaviour is
exercised rather than string-matched.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ash_captions.web.app import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to run studio_player.js")
SCRIPT = STATIC_DIR / "studio_player.js"

# A DOM small enough to construct the player: the elements it queries, the
# events it binds, and a fake JASSUB that records what it was asked to do.
HARNESS = """
const calls = [];
let timers = [];
global.setTimeout = (fn, ms) => { timers.push([ms, fn]); return timers.length; };
const runTimers = () => { const due = timers; timers = []; due.sort((a, b) => a[0] - b[0]).forEach(([, fn]) => fn()); };

function el(extra = {}) {
  return Object.assign({
    style: {}, classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(type, fn) { (this._on = this._on || {})[type] = fn; },
    removeEventListener() {}, appendChild() {}, setAttribute() {}, querySelector: () => el(),
    clientWidth: 800, clientHeight: 450, hidden: false, textContent: "",
  }, extra);
}
const video = el({ paused: PAUSED, currentTime: 12.5, videoWidth: 1920, videoHeight: 1080, play() {}, pause() {} });
const nodes = { video, stage: el(), frame: el(), "play-btn": el(), "seek": el(), "time": el(), "mute-btn": el() };
global.document = {
  getElementById: (id) => nodes[id] || el(),
  querySelector: (sel) => nodes[sel.replace("#", "")] || el(),
  addEventListener() {},
};
global.window = { addEventListener() {}, ResizeObserver: undefined };
global.ResizeObserver = undefined;
global.JASSUB = class {
  constructor(opts) { calls.push(["new", opts.subUrl]); }
  setTrackByUrl(url) { calls.push(["setTrackByUrl", url]); }
  setCurrentTime(paused, t) { calls.push(["setCurrentTime", paused, t]); }
  addEventListener() {}
};
"""


def run_player(*, paused: bool) -> list:
    """Build the player, attach a fake renderer, swap the track, and
    return every call the renderer received."""
    code = (
        HARNESS.replace("PAUSED", "true" if paused else "false")
        + f"const src = require('fs').readFileSync({json.dumps(str(SCRIPT))}, 'utf8');\n"
        + "eval(src);\n"
        + "const player = window.AshStudioPlayer.createPlayer({ stage: nodes.stage, frame: nodes.frame,"
        + " video, playBtn: el(), muteBtn: el(), seek: el({ value: 0 }), timeLabel: el() });\n"
        # load() waits for loadedmetadata; fire it so the renderer attaches.
        + "video._on.loadedmetadata && video._on.loadedmetadata();\n"
        + "player.load('/video.mp4', { assUrl: '/a.ass?v=1', fonts: [] });\n"
        + "video._on.loadedmetadata && video._on.loadedmetadata();\n"
        + "player.setTrack('/a.ass?v=2');\n"
        + "runTimers();\n"
        + "process.stdout.write(JSON.stringify(calls));"
    )
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        pytest.fail(f"node failed: {done.stderr[-1500:]}")
    return json.loads(done.stdout)


@needs_node
def test_a_paused_video_is_repainted_after_the_track_swaps():
    calls = run_player(paused=True)

    assert ["setTrackByUrl", "/a.ass?v=2"] in calls
    redraws = [c for c in calls if c[0] == "setCurrentTime"]
    assert redraws, "a paused video never got a redraw, so the old captions would stay"
    assert redraws[0][1] is True and redraws[0][2] == 12.5


@needs_node
def test_a_playing_video_is_left_alone():
    calls = run_player(paused=False)

    assert ["setTrackByUrl", "/a.ass?v=2"] in calls
    assert not [c for c in calls if c[0] == "setCurrentTime"], "a playing video redraws itself"
