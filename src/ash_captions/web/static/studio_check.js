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

  // ---- the panel ----

  function buildSkeleton(root) {
    root.innerHTML = "";
    const head = document.createElement("div");
    head.className = "check-head";
    const h2 = document.createElement("h2");
    h2.textContent = "Transcript";
    const lang = document.createElement("span");
    lang.className = "check-lang";
    lang.hidden = true;
    const uncertain = document.createElement("button");
    uncertain.type = "button";
    uncertain.className = "check-chip";
    uncertain.id = "check-uncertain";
    const spacer = document.createElement("span");
    spacer.className = "check-spacer";
    const toggle = document.createElement("label");
    toggle.className = "check-toggle";
    toggle.hidden = true;
    const showEn = document.createElement("input");
    showEn.type = "checkbox";
    showEn.id = "check-show-en";
    toggle.append(showEn, document.createTextNode(" Show English"));
    const translate = document.createElement("button");
    translate.type = "button";
    translate.className = "btn small";
    translate.id = "check-translate";
    translate.textContent = "Translate to check";
    translate.hidden = true;
    head.append(h2, lang, uncertain, spacer, toggle, translate);
    const list = document.createElement("div");
    list.className = "check-list";
    list.id = "check-list";
    list.setAttribute("aria-label", "Transcript lines");
    root.append(head, list);
    return { lang, uncertain, toggle, showEn, translate, list };
  }

  // refs: { jobId, job, player, live } -- handed over by studio.js once the
  // player exists. `live` false means the burned output is playing (the
  // original footage is gone), so nothing can be translated.
  function mount(refs) {
    const { jobId, job, player, live } = refs;
    const root = document.getElementById("check");
    if (!root || !player) return;
    const api = (suffix) => `/api/jobs/${encodeURIComponent(jobId)}${suffix}`;
    const els = buildSkeleton(root);
    const state = {
      words: [], en: null, cues: [], rows: [], enRows: null,
      rowEls: [], enEls: [], spanFor: new Map(),
      activeRow: -1, activeWord: -1, showEn: readShowEnglish(), watching: null,
    };

    async function fetchTranscript() {
      const res = await AshApi.request(api("/transcript"));
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't load the transcript"));
      return res.json();
    }

    async function fetchCues() {
      try {
        const res = await AshApi.request(api("/srt"));
        return res.ok ? parseSrt(await res.text()) : [];
      } catch (err) {
        return [];
      }
    }

    function setTranscript(transcript, cues) {
      state.words = transcript.words || [];
      state.en = Array.isArray(transcript.en_words) ? transcript.en_words : null;
      state.cues = cues.length ? cues : cuesFromWords(state.words, FALLBACK_WORDS_PER_ROW);
      state.rows = assignToCues(state.words, state.cues);
      state.enRows = state.en ? assignToCues(state.en, state.cues) : null;
      els.lang.textContent = (transcript.language || "").toUpperCase();
      els.lang.hidden = !transcript.language;
      renderHeader();
      renderRows();
    }

    function renderHeader() {
      const n = countUncertain(state.words);
      els.uncertain.textContent = n === 0 ? "No uncertain words" : `${n} uncertain word${n === 1 ? "" : "s"}`;
      els.uncertain.disabled = n === 0;
      els.uncertain.title = n === 0 ? "" : "Jump to the next uncertain word";
      const hasEnglish = state.en !== null;
      els.toggle.hidden = !hasEnglish;
      els.showEn.checked = state.showEn;
      els.translate.hidden = hasEnglish || !live;
      els.translate.title = "Run only the English pass on the saved transcript, then show it under every line";
    }

    function renderRows() {
      els.list.innerHTML = "";
      state.spanFor = new Map();
      state.enEls = [];
      state.rowEls = state.rows.map((rowWords, i) => {
        const row = document.createElement("div");
        row.className = "check-row";
        row.addEventListener("click", () => player.seek(state.cues[i].start));
        const src = document.createElement("div");
        src.className = "check-src";
        rowWords.forEach((w, k) => {
          const span = document.createElement("span");
          const kind = classify(w.p);
          span.className = kind ? `w ${kind}` : "w";
          span.textContent = w.w;
          if (kind) span.title = `${Math.round(w.p * 100)}% sure`;
          span.addEventListener("click", (e) => { e.stopPropagation(); player.seek(w.s); });
          if (k > 0) src.appendChild(document.createTextNode(" "));
          src.appendChild(span);
          state.spanFor.set(w, span);
        });
        const en = document.createElement("div");
        en.className = "check-en";
        en.textContent = state.enRows ? state.enRows[i].map((w) => w.w).join(" ") : "";
        row.append(src, en);
        state.enEls.push(en);
        els.list.appendChild(row);
        return row;
      });
      if (state.rowEls.length === 0) {
        const empty = document.createElement("p");
        empty.className = "check-empty";
        empty.textContent = "No words in the transcript.";
        els.list.appendChild(empty);
      }
      applyEnglishVisibility();
      state.activeRow = -1;
      state.activeWord = -1;
      sync(player.currentTime);
    }

    function applyEnglishVisibility() {
      const show = state.showEn && state.enRows !== null;
      for (const el of state.enEls) el.hidden = !show;
    }

    function markWord(i, on) {
      const el = state.spanFor.get(state.words[i]);
      if (el) el.classList.toggle("now", on);
    }

    // Vertical auto-scroll of the list only: scrollIntoView would also
    // scroll the page and the stage.
    function keepRowVisible(row) {
      const list = els.list;
      const top = row.offsetTop;
      const bottom = top + row.offsetHeight;
      if (top >= list.scrollTop && bottom <= list.scrollTop + list.clientHeight) return;
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      list.scrollTo({ top: Math.max(0, top - (list.clientHeight - row.offsetHeight) / 2), behavior: reduce ? "auto" : "smooth" });
    }

    function sync(t) {
      let rowIdx = -1;
      if (state.cues.length) {
        const idx = cueIndexFor(t, state.cues);
        const cue = state.cues[idx];
        rowIdx = t >= cue.start - 0.25 && t < cue.end + 0.25 ? idx : -1;
      }
      if (rowIdx !== state.activeRow) {
        if (state.activeRow >= 0) state.rowEls[state.activeRow].classList.remove("active");
        state.activeRow = rowIdx;
        if (rowIdx >= 0) {
          state.rowEls[rowIdx].classList.add("active");
          keepRowVisible(state.rowEls[rowIdx]);
        }
      }
      const wordIdx = wordIndexAt(state.words, t);
      if (wordIdx !== state.activeWord) {
        if (state.activeWord >= 0) markWord(state.activeWord, false);
        state.activeWord = wordIdx;
        if (wordIdx >= 0) markWord(wordIdx, true);
      }
    }

    function jumpToUncertain() {
      const idx = nextUncertain(state.words, player.currentTime);
      if (idx < 0) return;
      const w = state.words[idx];
      player.seek(w.s);
      sync(w.s);
      const el = state.spanFor.get(w);
      if (!el) return;
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1200);
    }

    function setTranslateState(status, pct) {
      const btn = els.translate;
      btn.innerHTML = "";
      if (status === "pending" || status === "running") {
        const spin = document.createElement("span");
        spin.className = "spinner";
        spin.setAttribute("aria-hidden", "true");
        btn.append(spin, document.createTextNode(status === "pending" ? " Queued…" : ` Translating… ${pct}%`));
        btn.disabled = true;
      } else {
        btn.textContent = "Translate to check";
        btn.disabled = false;
      }
    }

    async function translate() {
      if (els.translate.disabled) return;
      setTranslateState("pending", 0);
      try {
        const res = await AshApi.request(api("/translate"), { method: "POST" });
        if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't start the translation"));
        const created = await res.json();
        AshToast.show("Translating to English from the saved transcript. Watch it in the queue.", { ms: 8000 });
        watchTranslation(created.id);
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
        setTranslateState("idle", 0);
      }
    }

    // Polls the translate job (the same way studio.js watches a burn)
    // until it finishes, then reloads the transcript with its English.
    function watchTranslation(id) {
      if (state.watching) clearInterval(state.watching);
      setTranslateState("pending", 0);
      const poll = async () => {
        let latest;
        try {
          const res = await AshApi.request(`/api/jobs/${encodeURIComponent(id)}`);
          if (!res.ok) return;
          latest = await res.json();
        } catch (err) {
          return;
        }
        if (latest.status === "pending" || latest.status === "running") {
          setTranslateState(latest.status, Math.round((latest.progress || 0) * 100));
          return;
        }
        clearInterval(state.watching);
        state.watching = null;
        if (latest.status === "done") {
          let transcript = null;
          try { transcript = await fetchTranscript(); } catch (err) { transcript = null; }
          if (transcript && Array.isArray(transcript.en_words)) {
            state.showEn = true;
            writeShowEnglish(true);
            setTranscript(transcript, state.cues);
            AshToast.show("English is under every line now.", { kind: "ok" });
            return;
          }
          AshToast.show("The translation finished but no English words were saved.", { kind: "bad" });
        } else {
          AshToast.show(`The translation failed: ${latest.error || "something went wrong"}`, { kind: "bad", ms: 0 });
        }
        setTranslateState("idle", 0);
      };
      poll();
      state.watching = setInterval(poll, 2000);
    }

    // A translation of this footage already waiting or running (another
    // tab, or this page before a reload) is watched, not queued twice.
    async function watchExistingTranslation() {
      if (state.en !== null || !live) return;
      let jobs;
      try {
        const res = await AshApi.request("/api/jobs");
        if (!res.ok) return;
        jobs = await res.json();
      } catch (err) {
        return;
      }
      const sameInput = (j) => (job.input_path ? j.input_path === job.input_path : j.filename === job.filename);
      const inFlight = jobs.find((j) => j.id !== job.id && (j.status === "pending" || j.status === "running") && j.options && j.options.translate_to_english && sameInput(j));
      if (inFlight) watchTranslation(inFlight.id);
    }

    els.uncertain.addEventListener("click", jumpToUncertain);
    els.showEn.addEventListener("change", () => {
      state.showEn = els.showEn.checked;
      writeShowEnglish(state.showEn);
      applyEnglishVisibility();
    });
    els.translate.addEventListener("click", translate);

    (async () => {
      let transcript;
      try {
        transcript = await fetchTranscript();
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
        return;
      }
      if (!transcript || !transcript.words || transcript.words.length === 0) return; // an older job: no panel
      setTranscript(transcript, await fetchCues());
      root.hidden = false;
      player.onTime(sync);
      watchExistingTranslation();
    })();
  }

  const exported = {
    UNSURE, BAD, classify, countUncertain, parseSrt, cueIndexFor, assignToCues, cuesFromWords,
    nextUncertain, wordIndexAt, mount,
  };
  if (typeof window !== "undefined") window.AshStudioCheck = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})();
