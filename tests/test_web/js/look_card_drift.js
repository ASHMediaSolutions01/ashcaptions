// A driver, not a test: reads a JSON list of cases on stdin, runs each
// one through look_card_ass.js, and writes the answers to stdout as JSON.
//
// tests/test_web/test_look_card_drift.py feeds it the same cases it runs
// through styles/render.py and compares the two. That is the whole point:
// look_card_ass.test.js asserts against tag strings a person typed, so it
// keeps passing when the Python changes underneath it. This one cannot.
"use strict";

const path = require("node:path");

const ass = require(path.join(
  __dirname, "..", "..", "..", "src", "ash_captions", "web", "static", "look_card_ass.js"
));

let raw = "";
process.stdin.on("data", (chunk) => { raw += chunk; });
process.stdin.on("end", () => {
  const cases = JSON.parse(raw);
  const out = cases.map((c) => {
    switch (c.fn) {
      case "assStyleColour":
        return ass.assStyleColour(c.colour);
      case "assInlineColour":
        return ass.assInlineColour(c.colour);
      case "formatAssTime":
        return ass.formatAssTime(c.seconds);
      case "assAlignment":
        return ass.assAlignment(c.position, c.align);
      case "safeStyleName":
        return ass.safeStyleName(c.name);
      case "entranceTag":
        return ass.entranceTag(c.style, c.x, c.y, c.event_ms);
      case "exitTag":
        return ass.exitTag(c.style, c.x, c.y, c.event_ms);
      case "leadingOverride":
        return ass.leadingOverride(c.style, c.x, c.y, c.is_first, c.is_last, c.event_ms);
      case "activeWordTags":
        return ass.activeWordTags(c.style, c.active_colour, c.text_colour);
      default:
        throw new Error(`unknown function ${c.fn}`);
    }
  });
  process.stdout.write(JSON.stringify(out));
});
