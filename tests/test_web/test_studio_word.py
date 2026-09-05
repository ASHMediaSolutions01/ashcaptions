"""The Studio's per-word toolbar (static/studio_word.js + .css): the page
loads it, AshStudio.onReady hands it the player, and its pure helpers -- what
the look already gives a word, what actually differs from it, the PATCH body
-- behave. Run under node, which is on this machine, so the logic is
exercised rather than string-matched.

The PATCH itself is covered here too, against a fake fetch: the route is
track A's (`PATCH /api/jobs/{id}/transcript`, `set_style`), so what this
track owns is the *shape* it sends and what it does with a 409. End to end
through a live server is the integrator's after the merge.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ash_captions.web.app import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to run studio_word.js's helpers")
SCRIPT = STATIC_DIR / "studio_word.js"


def run_js(expression: str, preamble: str = ""):
    """Evaluate `expression` with the toolbar's exports bound to `w`."""
    code = (
        f"const w = require({json.dumps(str(SCRIPT))});\n"
        f"{preamble}\n"
        f"Promise.resolve({expression}).then(v => process.stdout.write(JSON.stringify(v)));"
    )
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


LOOK = {"colors": {"text": "#FFFFFF", "active": "#00E28A"}, "size": 72}
PLAIN = {"w": "hola", "s": 0.0, "e": 0.4, "p": 0.95}
STYLED = {"w": "mundo", "s": 0.4, "e": 0.9, "p": 0.9, "style": {"colour": "#FFD166", "scale": 1.25}}
WORDS = [PLAIN, STYLED, {"w": "amigo", "s": 1.0, "e": 1.5, "p": 0.9}]


@needs_node
class TestTheDiff:
    def test_the_bounds_match_the_schema(self):
        # WordStyle.scale is 0.5-3.0 in styles/schema.py.
        assert run_js("[w.MIN_PERCENT, w.MAX_PERCENT]") == [50, 300]

    def test_an_eight_digit_look_colour_becomes_six_for_the_picker(self):
        assert run_js('w.opaqueHex("#00000090", "#FFFFFF")') == "#000000"
        assert run_js('w.opaqueHex("#ffd166", "#FFFFFF")') == "#FFD166"
        assert run_js('w.opaqueHex(null, "#FFFFFF")') == "#FFFFFF"
        assert run_js('w.opaqueHex("nonsense", "#FFFFFF")') == "#FFFFFF"

    def test_percentages_are_clamped_to_the_schema_s_range(self):
        assert run_js("[10, 50, 125, 300, 999, 'x'].map(w.clampPercent)") == [50, 50, 125, 300, 300, 100]

    def test_the_look_alone_is_white_full_size_regular(self):
        assert run_js(f"w.lookBaseline({json.dumps(LOOK)})") == {
            "colour": "#FFFFFF", "percent": 100, "bold": False, "italic": False,
        }

    def test_a_word_with_no_override_shows_the_look_s_own_values(self):
        # The toolbar is a diff, not a blank form.
        assert run_js(f"w.effectiveStyle({json.dumps(PLAIN)}, {json.dumps(LOOK)})") == {
            "colour": "#FFFFFF", "percent": 100, "bold": False, "italic": False,
        }

    def test_a_word_with_an_override_shows_it_laid_over_the_look(self):
        assert run_js(f"w.effectiveStyle({json.dumps(STYLED)}, {json.dumps(LOOK)})") == {
            "colour": "#FFD166", "percent": 125, "bold": False, "italic": False,
        }

    def test_the_dot_follows_any_set_field(self):
        assert run_js(f"[{json.dumps(PLAIN)}, {json.dumps(STYLED)}].map(w.hasOverride)") == [False, True]
        assert run_js('w.hasOverride({w: "x", style: {}})') is False
        assert run_js('w.hasOverride({w: "x", style: {bold: false}})') is True

    def test_only_what_differs_from_the_look_is_sent(self):
        # Bold a word and its colour still follows the look.
        chosen = {"colour": "#FFFFFF", "percent": 100, "bold": True, "italic": False}
        assert run_js(f"w.diffStyle({json.dumps(chosen)}, {json.dumps(LOOK)}, null)") == {"bold": True}

    def test_matching_the_look_exactly_is_no_override_at_all(self):
        chosen = {"colour": "#ffffff", "percent": 100, "bold": False, "italic": False}
        assert run_js(f"w.diffStyle({json.dumps(chosen)}, {json.dumps(LOOK)}, null)") is None

    def test_a_full_diff_carries_every_changed_property(self):
        chosen = {"colour": "#FFD166", "percent": 125, "bold": True, "italic": True}
        assert run_js(f"w.diffStyle({json.dumps(chosen)}, {json.dumps(LOOK)}, null)") == {
            "colour": "#FFD166", "scale": 1.25, "bold": True, "italic": True,
        }

    def test_free_placement_survives_a_rewrite_of_the_word(self):
        # x/y are track F's; this toolbar never sets them and must not drop them.
        chosen = {"colour": "#FFFFFF", "percent": 100, "bold": True, "italic": False}
        keep = {"w": "x", "style": {"x": 0.2, "y": 0.8}}
        assert run_js(f"w.diffStyle({json.dumps(chosen)}, {json.dumps(LOOK)}, {json.dumps(keep)})") == {
            "bold": True, "x": 0.2, "y": 0.8,
        }


@needs_node
class TestTheOps:
    def test_the_patch_body_is_the_documented_shape(self):
        assert run_js('w.patchBody(3, 12, {colour: "#FFD166"})') == {
            "revision": 3,
            "ops": [{"op": "set_style", "index": 12, "style": {"colour": "#FFD166"}}],
        }

    def test_resetting_a_word_sends_a_null_style(self):
        assert run_js("w.patchBody(3, 12, null)") == {
            "revision": 3, "ops": [{"op": "set_style", "index": 12, "style": None}],
        }

    def test_reset_all_touches_only_the_words_that_carry_one(self):
        assert run_js(f"w.resetAllOps({json.dumps(WORDS)})") == [
            {"op": "set_style", "index": 1, "style": None}
        ]
        assert run_js(f"w.resetAllOps([{json.dumps(PLAIN)}])") == []
        assert run_js("w.resetAllOps(null)") == []

    def test_the_word_on_screen_at_the_playhead(self):
        assert run_js(f"[0.1, 0.5, 0.95, 1.2].map(t => w.wordIndexAt({json.dumps(WORDS)}, t))") == [0, 1, -1, 2]


FAKE_FETCH = """
function fakeFetch(seen, response) {
  return async (url, init) => {
    seen.push({ url, method: init && init.method, headers: (init && init.headers) || {},
                body: init && init.body ? JSON.parse(init.body) : null });
    return response;
  };
}
const ok = { ok: true, status: 200, json: async () => ({ revision: 4, captions: 12 }) };
const stale = { ok: false, status: 409, json: async () => ({ revision: 9, words: [] }) };
const broken = { ok: false, status: 500, json: async () => ({}) };
"""


@needs_node
class TestSaving:
    def test_one_patch_with_the_documented_body(self):
        seen = run_js(
            "(async () => { const seen = []; await w.saveStyle({request: fakeFetch(seen, ok),"
            ' jobId: "job-1", revision: 3, index: 12, style: {colour: "#FFD166"}}); return seen; })()',
            FAKE_FETCH,
        )
        assert len(seen) == 1
        assert seen[0]["url"] == "/api/jobs/job-1/transcript"
        assert seen[0]["method"] == "PATCH"
        assert seen[0]["headers"]["Content-Type"] == "application/json"
        assert seen[0]["body"] == {
            "revision": 3,
            "ops": [{"op": "set_style", "index": 12, "style": {"colour": "#FFD166"}}],
        }

    def test_no_route_of_its_own_is_ever_called(self):
        # set_style rides track A's transcript endpoint; this track adds none.
        seen = run_js(
            "(async () => { const seen = []; await w.sendOps({request: fakeFetch(seen, ok),"
            ' jobId: "job-1", revision: 0, ops: [{op: "set_style", index: 0, style: null}]}); return seen; })()',
            FAKE_FETCH,
        )
        assert [s["url"] for s in seen] == ["/api/jobs/job-1/transcript"]

    def test_the_new_revision_comes_back(self):
        out = run_js(
            "(async () => w.saveStyle({request: fakeFetch([], ok), jobId: 'j', revision: 3, index: 0,"
            " style: null}))()",
            FAKE_FETCH,
        )
        assert out == {"conflict": False, "result": {"revision": 4, "captions": 12}}

    def test_a_stale_revision_reports_a_conflict_instead_of_clobbering(self):
        out = run_js(
            "(async () => w.saveStyle({request: fakeFetch([], stale), jobId: 'j', revision: 1, index: 0,"
            ' style: {bold: true}}))()',
            FAKE_FETCH,
        )
        assert out == {"conflict": True, "current": {"revision": 9, "words": []}}

    def test_any_other_failure_raises(self):
        out = run_js(
            "(async () => { try { await w.saveStyle({request: fakeFetch([], broken), jobId: 'j',"
            " revision: 1, index: 0, style: null}); return 'no error'; }"
            " catch (e) { return 'raised'; } })()",
            FAKE_FETCH,
        )
        assert out == "raised"


@needs_node
class TestTheFilesAreWiredIn:
    def test_the_page_loads_the_script_and_has_the_mount_element(self):
        page = (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
        assert 'src="/static/studio_word.js?v=__VERSION__"' in page
        assert 'id="word-toolbar"' in page

    def test_the_stylesheet_ships_and_names_the_dot(self):
        css = (STATIC_DIR / "studio_word.css").read_text(encoding="utf-8")
        assert ".word-override" in css
        assert ".word-toolbar" in css

    def test_the_scope_line_and_both_resets_are_in_the_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "Changing " in source and "this word" in source and " only. " in source
        assert "Change the look instead" in source
        assert "Reset word" in source
        assert "Reset all overrides on this job" in source

    def test_font_and_outline_are_never_sent(self):
        # The design's line: per-word colour, size, weight and slant; font
        # family and outline stay properties of the look. Whatever the
        # controls hold, the diff can only carry WordStyle's own fields.
        chosen = {"colour": "#FFD166", "percent": 300, "bold": True, "italic": True}
        keep = {"w": "x", "style": {"x": 0.2, "y": 0.8, "colour": "#000000"}}
        keys = run_js(
            f"Object.keys(w.diffStyle({json.dumps(chosen)}, {json.dumps(LOOK)}, {json.dumps(keep)}))"
        )
        assert set(keys) <= {"colour", "scale", "bold", "italic", "x", "y"}
        assert "font" not in keys and "outline" not in keys
