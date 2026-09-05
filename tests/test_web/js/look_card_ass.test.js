// v0.6 track D, task 2: the sample .ass builder behind look_card.js is a
// hand-kept port of styles/render.py's tag formulas (see that file's
// module docstring). This exercises the pure functions with no DOM,
// asserting the exact tag shapes the Python emits for the same inputs --
// run with `node --test tests/test_web/js/look_card_ass.test.js` (wired
// into the Python suite by test_look_card_js.py).
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const ass = require(path.join(
  __dirname, "..", "..", "..", "src", "ash_captions", "web", "static", "look_card_ass.js"
));

function baseStyle(overrides) {
  return Object.assign(
    {
      name: "TEST LOOK",
      font: "Inter",
      size: 72,
      uppercase: false,
      letter_spacing: 0,
      colors: { text: "#FFFFFF", active: "#FFD166", outline: "#000000", shadow: "#00000090", box: "#00000000" },
      active_word: { effect: "pop", scale: 1.12, box: false },
      entrance: { effect: "fade", duration_ms: 120 },
      exit: { effect: "none", duration_ms: 0 },
      layout: { position: "bottom", max_words: 4, margin_l: 80, margin_r: 80, margin_v: 120, align: "center" },
    },
    overrides
  );
}

test("assStyleColour matches ASS's &HAABBGGRR, alpha inverted", () => {
  assert.equal(ass.assStyleColour("#FFFFFF"), "&H00FFFFFF");
  assert.equal(ass.assStyleColour("#000000"), "&H00000000");
  assert.equal(ass.assStyleColour("#00000090"), "&H6F000000"); // 255-144=111=0x6F
});

test("assInlineColour matches ASS's &HBBGGRR&", () => {
  assert.equal(ass.assInlineColour("#FFD166"), "&H66D1FF&");
});

test("formatAssTime matches h:mm:ss.cc", () => {
  assert.equal(ass.formatAssTime(0), "0:00:00.00");
  assert.equal(ass.formatAssTime(61.23), "0:01:01.23");
});

test("entranceTag: fade emits \\fad(duration,0), clamped to the event", () => {
  const style = baseStyle({ entrance: { effect: "fade", duration_ms: 120 } });
  assert.equal(ass.entranceTag(style, 540, 1800, 500), "\\fad(120,0)");
  assert.equal(ass.entranceTag(style, 540, 1800, 60), "\\fad(60,0)"); // clamped to eventMs
});

test("entranceTag: rise moves up from RISE_OFFSET_PX=46 below the anchor", () => {
  const style = baseStyle({ entrance: { effect: "rise", duration_ms: 140 } });
  assert.equal(ass.entranceTag(style, 540, 1800, 500), "\\move(540,1846,540,1800,0,140)");
});

test("entranceTag: slide moves in from SLIDE_OFFSET_PX=160 to the side", () => {
  const style = baseStyle({ entrance: { effect: "slide", duration_ms: 140 } });
  assert.equal(ass.entranceTag(style, 540, 1800, 500), "\\move(700,1800,540,1800,0,140)");
});

test("exitTag: rise moves up and out, timed against the event's own end", () => {
  const style = baseStyle({ exit: { effect: "rise", duration_ms: 100 } });
  assert.equal(ass.exitTag(style, 540, 1800, 500), "\\move(540,1800,540,1754,400,500)");
});

test("exitTag: 'none' and zero duration both emit nothing", () => {
  const style = baseStyle({ exit: { effect: "none", duration_ms: 500 } });
  assert.equal(ass.exitTag(style, 540, 1800, 500), "");
  const zero = baseStyle({ exit: { effect: "fade", duration_ms: 0 } });
  assert.equal(ass.exitTag(zero, 540, 1800, 500), "");
});

test("leadingOverride merges a one-event fade entrance+exit into one \\fad", () => {
  // Mirrors render.py's karaoke/one-word-card case: entrance and exit are
  // both "fad" tags on the same event, so they combine into \fad(in,out)
  // rather than emitting two \fad tags (which don't compose in libass).
  const style = baseStyle({
    entrance: { effect: "fade", duration_ms: 120 },
    exit: { effect: "fade", duration_ms: 100 },
  });
  assert.equal(ass.leadingOverride(style, 540, 1800, true, true, 2000), "\\fad(120,100)");
});

test("leadingOverride splits the event in half when both fades don't fit", () => {
  const style = baseStyle({
    entrance: { effect: "fade", duration_ms: 120 },
    exit: { effect: "fade", duration_ms: 100 },
  });
  assert.equal(ass.leadingOverride(style, 540, 1800, true, true, 150), "\\fad(75,75)");
});

test("activeWordTags: pop scales up then back to 100 in the active colour", () => {
  const style = baseStyle({ active_word: { effect: "pop", scale: 1.12, box: false } });
  const [open, close] = ass.activeWordTags(style, "&H66D1FF&", "&HFFFFFF&");
  assert.equal(open, "\\c&H66D1FF&\\t(0,90,\\fscx112\\fscy112)\\t(90,180,\\fscx100\\fscy100)");
  assert.equal(close, "\\c&HFFFFFF&\\fscx100\\fscy100");
});

test("safeStyleName strips commas and spaces, ASS's Style-name rules", () => {
  assert.equal(ass.safeStyleName("Glow, Mint"), "Glow_Mint");
  assert.equal(ass.safeStyleName(""), "STYLE");
});

test("buildSampleAss: a standard (pop) look gets one event per word, header first", () => {
  const style = baseStyle();
  const out = ass.buildSampleAss(style);
  assert.match(out, /^\[Script Info\]/);
  assert.match(out, /PlayResX: 1080/);
  assert.match(out, /PlayResY: 1920/);
  const dialogues = out.split("\n").filter((line) => line.startsWith("Dialogue:"));
  assert.equal(dialogues.length, ass.SAMPLE_WORDS.length);
  assert.match(dialogues[0], /\\fad\(120,0\)/); // entrance only on the first word
});

test("buildSampleAss: karaoke is one Dialogue event with a \\kf run per word", () => {
  const style = baseStyle({ active_word: { effect: "karaoke", scale: 1.0, box: false } });
  const out = ass.buildSampleAss(style);
  const dialogues = out.split("\n").filter((line) => line.startsWith("Dialogue:"));
  assert.equal(dialogues.length, 1);
  const kfCount = (dialogues[0].match(/\\kf\d+/g) || []).length;
  assert.equal(kfCount, ass.SAMPLE_WORDS.length);
});

test("buildSampleAss: box/scale_box renders each word in the _BOX companion style", () => {
  const style = baseStyle({ active_word: { effect: "box", scale: 1.0, box: false } });
  const out = ass.buildSampleAss(style);
  const dialogues = out.split("\n").filter((line) => line.startsWith("Dialogue:"));
  assert.equal(dialogues.length, ass.SAMPLE_WORDS.length);
  for (const line of dialogues) assert.match(line, /,TEST_LOOK_BOX,/);
});

test("buildSampleAss: glow doubles every word into a halo layer and a text layer", () => {
  const style = baseStyle({ active_word: { effect: "glow", scale: 1.12, box: false } });
  const out = ass.buildSampleAss(style);
  const dialogues = out.split("\n").filter((line) => line.startsWith("Dialogue:"));
  assert.equal(dialogues.length, ass.SAMPLE_WORDS.length * 2);
  const layers = dialogues.map((line) => line.split(",")[0]);
  assert.deepEqual(layers.slice(0, 2), ["Dialogue: 0", "Dialogue: 1"]);
});

test("buildSampleAss never throws on any active-word effect the schema allows", () => {
  const effects = ["none", "pop", "box", "scale_box", "card_box", "karaoke", "shake", "glow"];
  for (const effect of effects) {
    const style = baseStyle({ active_word: { effect, scale: 1.1, box: false } });
    assert.doesNotThrow(() => ass.buildSampleAss(style), `effect ${effect} threw`);
  }
});
