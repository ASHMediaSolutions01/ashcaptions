/* Studio caption check (v0.5): the transcript panel as a two-line list --
   the source words, and the English words under them once the job has
   been translated -- synced to the playhead. Words the transcriber was
   unsure of are underlined (amber below 0.5, red below 0.3) and a header
   chip jumps to the next one. "Translate to check" runs only the English
   pass on the saved transcript. No text editing: the panel tells the
   editor where to look; the fix stays the client glossary or a re-run.

   The helpers above mount() are pure and also exported for node, so
   tests/test_web/test_studio_check.py runs them without a browser;
   nothing here touches the DOM until studio.js calls mount(). */
(function () {
  "use strict";

  const UNSURE = 0.5;
  const BAD = 0.3;
  const PAUSE_SECONDS = 0.8;
  const FALLBACK_WORDS_PER_ROW = 8;
  const SHOW_ENGLISH_KEY = "ash.studioShowEnglish";

  // ---- pure helpers (no DOM) ----

  // "" (confident), "unsure" (below 0.5) or "bad" (below 0.3). English
  // words carry no p and are never flagged.
  function classify(p) {
    if (typeof p !== "number") return "";
    return p < BAD ? "bad" : p < UNSURE ? "unsure" : "";
  }

  function countUncertain(words) {
    return words.filter((w) => classify(w.p) !== "").length;
  }

  function srtTime(text) {
    const m = /(\d+):(\d+):(\d+)[,.](\d+)/.exec(text);
    return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000 : 0;
  }

  function parseSrt(text) {
    const cues = [];
    for (const block of text.replace(/\r/g, "").split(/\n\n+/)) {
      const lines = block.split("\n").filter((l) => l.trim());
      const timeIdx = lines.findIndex((l) => l.includes("-->"));
      if (timeIdx < 0) continue;
      const [start, end] = lines[timeIdx].split("-->");
      const body = lines.slice(timeIdx + 1).join(" ").trim();
      if (body) cues.push({ start: srtTime(start), end: srtTime(end), text: body });
    }
    return cues;
  }

  // The row a word belongs to: the last cue that starts at or before the
  // word. Translated words never share the source timings exactly, so
  // "contained in the cue" would lose the ones that land in a gap.
  function cueIndexFor(start, cues) {
    let idx = 0;
    for (let i = 0; i < cues.length; i += 1) {
      if (cues[i].start <= start + 1e-6) idx = i;
      else break;
    }
    return idx;
  }

  function assignToCues(words, cues) {
    const rows = cues.map(() => []);
    if (cues.length === 0) return rows;
    for (const w of words) rows[cueIndexFor(w.s, cues)].push(w);
    return rows;
  }

  // Without an .srt (a job from an older build) the words group
  // themselves: a new row at every pause, or every maxWords words.
  function cuesFromWords(words, maxWords) {
    const cues = [];
    let current = null;
    for (const w of words) {
      const pause = current !== null && w.s - current.end > PAUSE_SECONDS;
      if (current === null || pause || current.count >= maxWords) {
        current = { start: w.s, end: w.e, count: 0 };
        cues.push(current);
      }
      current.end = Math.max(current.end, w.e);
      current.count += 1;
    }
    return cues.map((c) => ({ start: c.start, end: c.end }));
  }

  // The first uncertain word after `afterTime`, wrapping to the first one
  // in the transcript; -1 when there are none.
  function nextUncertain(words, afterTime) {
    const after = words.findIndex((w) => classify(w.p) !== "" && w.s > afterTime + 0.05);
    return after >= 0 ? after : words.findIndex((w) => classify(w.p) !== "");
  }

  function wordIndexAt(words, t) {
    for (let i = 0; i < words.length; i += 1) {
      if (t >= words[i].s && t < words[i].e) return i;
      if (words[i].s > t) return -1;
    }
    return -1;
  }

  function readShowEnglish() {
    try { return localStorage.getItem(SHOW_ENGLISH_KEY) !== "0"; } catch (err) { return true; }
  }
  function writeShowEnglish(on) {
    try { localStorage.setItem(SHOW_ENGLISH_KEY, on ? "1" : "0"); } catch (err) { /* private mode */ }
  }

  // ---- the panel (Task 6 fills this in) ----

  function mount(refs) {
    void refs;
  }

  const exported = {
    UNSURE, BAD, classify, countUncertain, parseSrt, cueIndexFor, assignToCues, cuesFromWords,
    nextUncertain, wordIndexAt, mount,
  };
  if (typeof window !== "undefined") window.AshStudioCheck = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})();
