"""The Studio's editable transcript panel (static/studio_edit.js + .css):
the page loads it, and its pure helpers -- the occurrence rule the "Fix
every ..." count comes from, the retime clamp, the line assignment and the
too-long warning -- behave. Run under node, which is on this machine, so
the logic is exercised rather than string-matched.

The occurrence rule and the clamp exist twice, once here and once in
`app/transcript.py`; the tests below use the same words as
`tests/test_app/test_transcript_edits.py` so the two cannot drift apart
without one of them going red."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ash_captions.web.app import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to run studio_edit.js's helpers")
SCRIPT = STATIC_DIR / "studio_edit.js"

WORDS = [
    {"w": "Coge", "s": 0.0, "e": 0.40, "p": 0.95},
    {"w": "la", "s": 0.40, "e": 0.55, "p": 0.99},
    {"w": "haramienta,", "s": 0.55, "e": 1.10, "p": 0.41},
    {"w": "la", "s": 1.20, "e": 1.35, "p": 0.98},
    {"w": "HARAMIENTA", "s": 1.35, "e": 1.90, "p": 0.52},
    {"w": "grande", "s": 1.90, "e": 2.40, "p": 0.97},
]
SRT = (
    "1\n00:00:00,000 --> 00:00:01,100\nCoge la haramienta,\n\n"
    "2\n00:00:01,200 --> 00:00:02,400\nla HARAMIENTA grande\n"
)


def run_js(expression: str):
    """Evaluate `expression` with the panel's exports bound to `e`."""
    code = f"const e = require({json.dumps(str(SCRIPT))}); process.stdout.write(JSON.stringify({expression}));"
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


@needs_node
class TestTheWordItself:
    def test_punctuation_is_split_off_the_core_the_same_way_the_server_does(self):
        assert run_js('e.splitWordText("haramienta,")') == ["", "haramienta", ","]
        assert run_js('e.splitWordText("¿qué?")') == ["¿", "qué", "?"]
        assert run_js("e.splitWordText(\"don't\")") == ["", "don't", ""]

    def test_a_non_ascii_letter_is_a_letter(self):
        # \W in JavaScript is ASCII-only; getting this wrong would cut
        # "qué" into "qu" + "é" and make the occurrence count a lie.
        assert run_js('e.splitWordText("herramienta")') == ["", "herramienta", ""]
        assert run_js('e.splitWordText("ação")') == ["", "ação", ""]

    def test_the_occurrences_own_capitalisation_wins(self):
        assert run_js('["haramienta","Haramienta","HARAMIENTA"].map(s => e.applyCase(s, "herramienta"))') == [
            "herramienta",
            "Herramienta",
            "HERRAMIENTA",
        ]

    def test_occurrences_ignores_case_and_the_punctuation_around_the_word(self):
        assert run_js(f"e.occurrences({json.dumps(WORDS)}, 2)") == [2, 4]
        assert run_js(f"e.occurrences({json.dumps(WORDS)}, 1)") == [1, 3]

    def test_a_word_that_is_only_punctuation_matches_only_itself(self):
        words = [{"w": "--", "s": 0.0, "e": 0.2, "p": 0.5}, {"w": "--", "s": 0.3, "e": 0.5, "p": 0.5}]
        assert run_js(f"e.occurrences({json.dumps(words)}, 0)") == [0]


@needs_node
class TestRetimeClamp:
    def test_an_edge_cannot_cross_its_neighbour(self):
        assert run_js(f"e.clampRetime({json.dumps(WORDS)}, 3, {{start: 0}})")["start"] == 1.1
        assert run_js(f"e.clampRetime({json.dumps(WORDS)}, 3, {{end: 9}})")["end"] == 1.35

    def test_a_word_is_never_squeezed_below_the_minimum(self):
        got = run_js(f"e.clampRetime({json.dumps(WORDS)}, 5, {{start: 2.399}})")
        assert round(got["end"] - got["start"], 3) >= run_js("e.MIN_WORD_SECONDS")

    def test_the_last_word_may_run_on_because_nothing_follows_it(self):
        assert run_js(f"e.clampRetime({json.dumps(WORDS)}, 5, {{end: 9}})")["end"] == 9

    def test_it_agrees_with_the_server_on_the_same_numbers(self):
        from ash_captions.app.transcript import TranscriptRecord, retime
        from ash_captions.engine import Segment, Word

        words = tuple(Word(w["w"], w["s"], w["e"], w["p"]) for w in WORDS)
        record = TranscriptRecord(
            language="es", words=words, segments=(Segment("x", 0.0, 2.4, words),)
        )
        for index, change in ((3, {"start": 0.0}), (3, {"end": 9.0}), (5, {"start": 2.399})):
            server = retime(record, index, **change).words[index]
            browser = run_js(f"e.clampRetime({json.dumps(WORDS)}, {index}, {json.dumps(change)})")
            assert (browser["start"], browser["end"]) == (server.start, server.end), change


@needs_node
class TestLines:
    def test_words_land_on_the_line_the_server_actually_wrote(self):
        cues = run_js(f"e.parseSrt({json.dumps(SRT)})")
        assert len(cues) == 2
        assert run_js(f"e.assignToLines({json.dumps(WORDS)}, e.parseSrt({json.dumps(SRT)}))") == [[0, 1, 2], [3, 4, 5]]

    def test_without_an_srt_every_word_is_one_line(self):
        assert run_js(f"e.assignToLines({json.dumps(WORDS)}, [])") == [[0, 1, 2, 3, 4, 5]]
        assert run_js("e.assignToLines([], [])") == []

    def test_a_line_longer_than_the_look_says_so_and_names_it(self):
        assert "5 words" in run_js('e.tooLongWarning([1,2,3,4,5], 4, "CLEAN")')
        assert "CLEAN" in run_js('e.tooLongWarning([1,2,3,4,5], 4, "CLEAN")')
        assert run_js('e.tooLongWarning([1,2,3,4], 4, "CLEAN")') == ""
        assert run_js('e.tooLongWarning([1,2,3,4,5], 0, "CLEAN")') == ""

    def test_the_confidence_thresholds_match_the_caption_check(self):
        assert run_js("[e.UNSURE, e.BAD]") == [0.5, 0.3]
        assert run_js("[0.9, 0.5, 0.49, 0.3, 0.29, null].map(e.classify)") == ["", "", "unsure", "unsure", "bad", ""]

    def test_times_read_as_times(self):
        assert run_js("[0, 1.5, 61.234].map(e.formatSeconds)") == ["0:00.00", "0:01.50", "1:01.23"]


@needs_node
def test_the_panel_publishes_what_the_other_tracks_call():
    exported = run_js("Object.keys(e)")
    assert "onWordEdited" in exported and "reload" in exported and "subscribe" in exported
    assert run_js("typeof e.onWordEdited") == "function"
    # Calling it with no panel mounted must be a no-op, not a crash: track B
    # may report an edit before this panel has been mounted.
    assert run_js("(e.onWordEdited(3), true)") is True


def test_the_studio_page_loads_the_panel_and_gives_it_somewhere_to_mount():
    html = (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
    assert '<div id="transcript-edit"></div>' in html
    assert '<script src="/static/studio_edit.js?v=__VERSION__"></script>' in html
    # The stylesheet rides with the script, so the page keeps a two-line diff.
    assert "studio_edit.css" not in html
    assert "/static/studio_edit.css" in SCRIPT.read_text(encoding="utf-8")


def test_the_page_still_has_no_hand_typed_version(client, app):
    res = client.get(f"/studio/{'x'}")
    assert res.status_code == 200
    assert f"/static/studio_edit.js?v={app.state.version}" in res.text
    assert "__VERSION__" not in res.text


def test_the_panel_stays_under_the_five_hundred_line_ceiling():
    assert len(SCRIPT.read_text(encoding="utf-8").splitlines()) < 500


POP_SCRIPT = STATIC_DIR / "studio_edit_pop.js"


def place(word_box, *, window=(1366, 768), pop=(280, 150)):
    """Where studio_edit_pop.js puts the popup for a word at `word_box`.

    Run under Node with the handful of browser things the module touches
    stubbed out -- it does geometry and nothing else, which is why it is
    its own file.
    """
    stub = f"""
      const box = {json.dumps(word_box)};
      global.window = {{ innerWidth: {window[0]}, innerHeight: {window[1]}, scrollX: 0, scrollY: 0 }};
      const pop = {{ offsetWidth: {pop[0]}, offsetHeight: {pop[1]}, hidden: false, style: {{}} }};
      const span = {{ getBoundingClientRect: () => box }};
      const m = require({json.dumps(str(POP_SCRIPT))});
      m.place(pop, span);
      process.stdout.write(JSON.stringify(pop.style));
    """
    done = subprocess.run([NODE, "-e", stub], capture_output=True, text=True, encoding="utf-8", check=True)
    style = json.loads(done.stdout)
    return int(style["left"].rstrip("px")), int(style["top"].rstrip("px"))


@needs_node
class TestWhereThePopupGoes:
    """The editor for one word, positioned in the page so the scrolling
    list cannot clip it -- which means it has to mind the window itself."""

    def test_it_sits_just_under_its_word(self):
        left, top = place({"left": 700, "top": 300, "bottom": 320, "right": 740})
        assert (left, top) == (700, 326)

    def test_it_flips_above_the_word_rather_than_off_the_bottom(self):
        """On the last lines of a long transcript the buttons were simply
        unreachable: the popup was drawn past the foot of the window."""
        left, top = place({"left": 700, "top": 700, "bottom": 720, "right": 740})
        assert top == 700 - 150 - 6
        assert top + 150 < 768

    def test_a_word_too_near_the_top_to_flip_still_lands_on_screen(self):
        _, top = place({"left": 700, "top": 40, "bottom": 700, "right": 740})
        assert top >= 8

    def test_it_never_runs_off_the_right_edge(self):
        left, _ = place({"left": 1300, "top": 300, "bottom": 320, "right": 1340})
        assert left == 1366 - 280 - 8

    def test_it_never_runs_off_the_left_edge(self):
        left, _ = place({"left": -40, "top": 300, "bottom": 320, "right": 10})
        assert left == 8

    def test_the_page_scroll_is_added_because_the_popup_is_absolute(self):
        stub = """
          global.window = { innerWidth: 1366, innerHeight: 768, scrollX: 5, scrollY: 60 };
          const pop = { offsetWidth: 280, offsetHeight: 150, hidden: false, style: {} };
          const span = { getBoundingClientRect: () => ({ left: 700, top: 300, bottom: 320, right: 740 }) };
        """
        code = stub + (f"const m = require({json.dumps(str(POP_SCRIPT))}); m.place(pop, span);"
                       " process.stdout.write(JSON.stringify(pop.style));")
        done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
        style = json.loads(done.stdout)
        assert style["top"] == "386px"  # 320 + 6 + 60
        assert style["left"] == "705px"  # 700 + 5
