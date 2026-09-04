"""The Studio caption-check panel (static/studio_check.js + .css): the page
loads it, studio.js hands it the player, and its pure helpers (thresholds,
cue assignment, uncertain-word navigation) behave -- run under node, which
is on this machine, so the logic is exercised rather than string-matched."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ash_captions.web.app import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to run studio_check.js's helpers")
SCRIPT = STATIC_DIR / "studio_check.js"


def run_js(expression: str):
    """Evaluate `expression` with the panel's exports bound to `c`."""
    code = f"const c = require({json.dumps(str(SCRIPT))}); process.stdout.write(JSON.stringify({expression}));"
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


WORDS = [
    {"w": "hola", "s": 0.0, "e": 0.4, "p": 0.95},
    {"w": "mundo", "s": 0.4, "e": 0.9, "p": 0.42},
    {"w": "qué", "s": 1.0, "e": 1.2, "p": 0.21},
    {"w": "tal", "s": 1.2, "e": 1.5, "p": 0.88},
    {"w": "amigo", "s": 2.6, "e": 3.0, "p": 0.49},
]
CUES = [{"start": 0.0, "end": 0.9}, {"start": 1.0, "end": 1.5}, {"start": 2.5, "end": 3.1}]
# Translated timings never line up exactly: "world" lands in the gap after
# cue 1 and "you" runs past cue 2's end.
ENGLISH = [
    {"w": "hello", "s": 0.0, "e": 0.4},
    {"w": "world", "s": 0.95, "e": 0.99},
    {"w": "how", "s": 1.0, "e": 1.2},
    {"w": "are", "s": 1.2, "e": 1.4},
    {"w": "you", "s": 1.4, "e": 1.6},
    {"w": "friend", "s": 2.55, "e": 3.0},
]
SRT = "1\n00:00:00,000 --> 00:00:00,900\nhola mundo\n\n2\n00:00:01,000 --> 00:00:01,500\nqué tal\n"


@needs_node
class TestHelpers:
    def test_thresholds_are_half_and_point_three(self):
        assert run_js("[c.UNSURE, c.BAD]") == [0.5, 0.3]
        assert run_js("[0.9, 0.5, 0.49, 0.3, 0.29, null].map(c.classify)") == ["", "", "unsure", "unsure", "bad", ""]

    def test_uncertain_count_matches_the_thresholds(self):
        assert run_js(f"c.countUncertain({json.dumps(WORDS)})") == 3
        assert run_js(f"c.countUncertain({json.dumps(ENGLISH)})") == 0

    def test_words_land_on_the_last_cue_that_started(self):
        rows = run_js(f"c.assignToCues({json.dumps(WORDS)}, {json.dumps(CUES)}).map(r => r.map(w => w.w))")
        assert rows == [["hola", "mundo"], ["qué", "tal"], ["amigo"]]
        english = run_js(f"c.assignToCues({json.dumps(ENGLISH)}, {json.dumps(CUES)}).map(r => r.map(w => w.w))")
        assert english == [["hello", "world"], ["how", "are", "you"], ["friend"]]
        assert run_js(f"c.assignToCues({json.dumps(WORDS)}, [])") == []

    def test_fallback_rows_split_at_pauses_and_at_max_words(self):
        words = []
        t = 0.0
        for i in range(12):
            if i == 4:
                t += 2.0  # a pause after the fourth word
            words.append({"w": f"w{i}", "s": t, "e": t + 0.5, "p": 1.0})
            t += 0.5
        cues = run_js(f"c.cuesFromWords({json.dumps(words)}, 5)")
        assert [(c["start"], c["end"]) for c in cues] == [(0.0, 2.0), (4.0, 6.5), (6.5, 8.0)]

    def test_next_uncertain_walks_forward_and_wraps(self):
        assert run_js(f"c.nextUncertain({json.dumps(WORDS)}, 0.0)") == 1
        assert run_js(f"c.nextUncertain({json.dumps(WORDS)}, 0.4)") == 2
        assert run_js(f"c.nextUncertain({json.dumps(WORDS)}, 1.0)") == 4
        assert run_js(f"c.nextUncertain({json.dumps(WORDS)}, 2.6)") == 1
        assert run_js(f"c.nextUncertain({json.dumps(ENGLISH)}, 0.0)") == -1

    def test_word_index_at_a_time(self):
        assert run_js(f"c.wordIndexAt({json.dumps(WORDS)}, 0.5)") == 1
        assert run_js(f"c.wordIndexAt({json.dumps(WORDS)}, 0.95)") == -1
        assert run_js(f"c.wordIndexAt({json.dumps(WORDS)}, 1.3)") == 3

    def test_parse_srt(self):
        cues = run_js(f"c.parseSrt({json.dumps(SRT)})")
        assert cues == [
            {"start": 0.0, "end": 0.9, "text": "hola mundo"},
            {"start": 1.0, "end": 1.5, "text": "qué tal"},
        ]

    def test_loading_the_script_touches_no_dom(self):
        # `require` under node has no `document`; if the script reached for
        # it at load time this would throw instead of answering.
        assert run_js("typeof c.mount") == "function"
