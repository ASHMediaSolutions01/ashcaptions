/* The transcript panel's rules, with no DOM: what studio_edit.js
   measures words, lines and times against. Its own file because the
   panel sits at the 500-line ceiling, and because these are exactly the
   parts tests/test_web/test_studio_edit_js.py runs under node.

   The occurrence rule and the retime clamp exist twice, once here and
   once in app/transcript.py; the tests use the same words for both so
   they cannot drift apart without one going red. */
(function () {
  "use strict";

  const MIN_WORD_SECONDS = 0.06;
  const SECONDS_PER_PIXEL = 1 / 200; // a second is a deliberate drag, not a twitch
  const DEFAULT_MAX_WORDS = 4;
  const UNSURE = 0.5, BAD = 0.3; // the caption check's confidence thresholds

  // ---- pure helpers (no DOM) ----

  function round3(v) { return Math.round(v * 1000) / 1000; }
  function srtTime(text) {
    const m = /(\d+):(\d+):(\d+)[,.](\d+)/.exec(text);
    return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000 : 0;
  }
  function parseSrt(text) {
    const cues = [];
    for (const block of text.replace(/\r/g, "").split(/\n\n+/)) {
      const lines = block.split("\n").filter((l) => l.trim());
      const idx = lines.findIndex((l) => l.includes("-->"));
      if (idx < 0) continue;
      const [start, end] = lines[idx].split("-->");
      cues.push({ start: srtTime(start), end: srtTime(end) });
    }
    return cues;
  }

  // The line a word belongs to: the last cue that starts at or before it.
  function lineIndexFor(start, cues) {
    let idx = 0;
    for (let i = 0; i < cues.length && cues[i].start <= start + 1e-6; i += 1) idx = i;
    return idx;
  }

  // Lines as arrays of word *indexes*, so every span knows which word it is.
  function assignToLines(words, cues) {
    if (!cues.length) return words.length ? [words.map((_w, i) => i)] : [];
    const lines = cues.map(() => []);
    words.forEach((w, i) => lines[lineIndexFor(w.s, cues)].push(i));
    return lines.filter((line) => line.length > 0);
  }

  // "haramienta," -> ["", "haramienta", ","]. Written out rather than with
  // \W, which in JavaScript is ASCII-only and would cut "qué" in half; the
  // server's rule is Unicode-aware and the two must agree, or the count in
  // the popup lies about what the button is going to change.
  const CORE_RE = /^([^\p{L}\p{N}_]*)([\s\S]*?)([^\p{L}\p{N}_]*)$/u;

  function splitWordText(text) {
    const m = CORE_RE.exec(text || "");
    return m ? [m[1], m[2], m[3]] : ["", text || "", ""];
  }

  function applyCase(sample, text) {
    if (!sample || !text) return text;
    const upper = sample === sample.toUpperCase() && sample !== sample.toLowerCase();
    if (upper && sample.length > 1) return text.toUpperCase();
    if (sample[0] === sample[0].toUpperCase() && sample[0] !== sample[0].toLowerCase()) {
      return text[0].toUpperCase() + text.slice(1);
    }
    return text[0].toLowerCase() + text.slice(1);
  }

  // Every word that is the same word as this one: same core, ignoring case
  // and the punctuation around it -- the rule the server applies, so the
  // count in the popup is the number of words the button will change.
  function occurrences(words, index) {
    const core = splitWordText(words[index] ? words[index].w : "")[1];
    if (!core) return [index];
    const key = core.toLowerCase();
    const out = [];
    words.forEach((w, i) => { if (splitWordText(w.w)[1].toLowerCase() === key) out.push(i); });
    return out;
  }

  // The server's clamp, in the browser, so a drag shows where it will land.
  function clampRetime(words, index, change) {
    const w = words[index];
    let start = change.start == null ? w.s : change.start;
    let end = change.end == null ? w.e : change.end;
    const floor = index > 0 ? words[index - 1].e : 0;
    const ceiling = index + 1 < words.length ? words[index + 1].s : Infinity;
    start = Math.max(start, floor);
    end = Math.min(end, ceiling);
    if (end - start < MIN_WORD_SECONDS) {
      if (change.start != null && change.end == null) start = Math.max(floor, end - MIN_WORD_SECONDS);
      else end = Math.min(ceiling, start + MIN_WORD_SECONDS);
    }
    return { start: round3(start), end: round3(end) };
  }

  function classify(p) {
    return typeof p !== "number" ? "" : p < BAD ? "bad" : p < UNSURE ? "unsure" : "";
  }

  function tooLongWarning(line, maxWords, lookName) {
    if (!maxWords || line.length <= maxWords) return "";
    return `${line.length} words on this line — ${lookName || "this look"} shows ${maxWords}.`;
  }
  function formatSeconds(t) {
    const whole = Math.floor(t);
    const ss = String(whole % 60).padStart(2, "0");
    const cs = String(Math.floor((t - whole) * 100)).padStart(2, "0");
    return `${Math.floor(whole / 60)}:${ss}.${cs}`;
  }

  // One line for the whole panel instead of a warning under every line.
  // Picking a one-word look used to put "3 words on this line -- HYPE
  // shows 1" under all forty-six lines, which doubled the column and
  // buried the uncertain-word underlines it exists to show.
  function lineSummary(lines, maxWords, lookName) {
    if (!maxWords) return "";
    const over = lines.filter((line) => line.length > maxWords).length;
    if (!over) return "";
    const name = lookName || "This look";
    const words = maxWords === 1 ? "1 word" : `${maxWords} words`;
    const which = lines.length === 1 ? "the line" : `${over} of ${lines.length} lines`;
    return `${name} shows ${words} at a time — ${which} will split across captions.`;
  }

  // A keyboard nudge of one edge, clamped exactly as a drag is.
  function nudgeRetime(words, index, edge, delta) {
    const w = words[index];
    return clampRetime(words, index, edge === "start" ? { start: w.s + delta } : { end: w.e + delta });
  }

  const exported = {
    MIN_WORD_SECONDS, SECONDS_PER_PIXEL, DEFAULT_MAX_WORDS, UNSURE, BAD,
    round3, srtTime, parseSrt, lineIndexFor, assignToLines, splitWordText, applyCase,
    occurrences, clampRetime, classify, tooLongWarning, formatSeconds, lineSummary, nudgeRetime,
  };
  if (typeof window !== "undefined") window.AshStudioEditRules = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})();
