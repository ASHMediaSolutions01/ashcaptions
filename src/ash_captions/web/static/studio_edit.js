/* Studio transcript editing (v0.6 §1): the words the transcriber got
   wrong, fixed where the editor notices them. Click a word and retype it,
   fix every occurrence at once, teach the client glossary so the next job
   is right, split or merge a line, or drag a word's edge to retime it.

   Every change is one PATCH /api/jobs/{id}/transcript carrying the record's
   revision, so a second tab is told to reload instead of clobbering; the
   server re-renders the .ass, .srt and .txt and this file reloads the
   caption track in place, exactly as picking a look does. The lines shown
   are the ones the server actually wrote -- the .srt is parsed and the
   words assigned to its cues -- so "this line is too long" is about a real
   caption and not a guess at one.

   Publishes `window.AshStudioEdit`: `onWordEdited(index)` -- call it after
   changing a word from elsewhere (track B's per-word toolbar does) and
   `subscribe(fn)` to be told when this panel changes one -- and `reload()`.
   The helpers above mount() are pure and also exported for node, so
   tests/test_web/test_studio_edit_js.py runs them without a browser. */
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

  // ---- the panel ----

  const listeners = [];
  function notify(index) {
    // One bad subscriber must not stop the rest.
    for (const fn of listeners) { try { fn(index); } catch (err) { /* keep going */ } }
  }

  // The stylesheet rides with the script, so studio.html keeps a two-line
  // diff (one script tag, one mount element) for the three-way merge.
  function ensureStylesheet() {
    if (document.querySelector("link[data-ash-edit-css]")) return;
    const existing = document.querySelector('link[href*="studio.css"]');
    const version = existing ? existing.getAttribute("href").split("?v=")[1] || "" : "";
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.setAttribute("data-ash-edit-css", "1");
    link.href = `/static/studio_edit.css${version ? `?v=${version}` : ""}`;
    document.head.appendChild(link);
  }

  const PANEL_HTML = [
    '<section class="tedit" hidden><div class="tedit-head"><h2>Words</h2>',
    '<span class="tedit-hint">Click a word to fix it. Drag its edges to change when it lands.</span>',
    '<span class="tedit-spacer"></span><span class="tedit-state"></span></div>',
    '<div class="tedit-list" aria-label="Transcript, editable"></div></section>',
  ].join("");

  const POPUP_HTML = [
    '<input class="tedit-input" type="text" aria-label="The word" autocomplete="off" spellcheck="false">',
    '<div class="tedit-row"><button type="button" class="btn small primary" data-act="one">Fix this one</button>',
    '<button type="button" class="btn small" data-act="all"></button></div>',
    '<button type="button" class="btn small quiet" data-act="glossary">Always spell it this way</button>',
    '<div class="tedit-row"><button type="button" class="btn small" data-act="split">Split line here</button>',
    '<button type="button" class="btn small" data-act="merge">Merge with the line above</button></div>',
    '<div class="tedit-times"></div>',
  ].join("");

  function mount(refs) {
    const root = document.getElementById("transcript-edit");
    if (!root) return null;
    const { jobId, player, getJob, assUrl } = refs;
    ensureStylesheet();  // studio.html carries no <link> of its own
    const api = (suffix) => `/api/jobs/${encodeURIComponent(jobId)}${suffix}`;
    root.innerHTML = PANEL_HTML;
    const section = root.querySelector(".tedit");
    const list = root.querySelector(".tedit-list");
    const stateLabel = root.querySelector(".tedit-state");
    const pop = document.createElement("div");
    pop.className = "tedit-pop";
    pop.hidden = true;
    pop.innerHTML = POPUP_HTML;
    document.body.appendChild(pop);
    const input = pop.querySelector(".tedit-input");
    const act = (name) => pop.querySelector(`[data-act="${name}"]`);
    const state = {
      words: [], meta: null, revision: 0, cues: [], lines: [], spans: [],
      maxWords: DEFAULT_MAX_WORDS, lookName: "", selected: -1, busy: false,
    };

    // ---- loading ----

    function setState(text, kind) {
      stateLabel.textContent = text || "";
      stateLabel.className = `tedit-state${kind ? ` ${kind}` : ""}`;
    }

    async function fetchJson(url) {
      const res = await AshApi.request(url);
      return res.ok ? res.json() : null;
    }

    async function fetchCues() {
      try {
        const res = await AshApi.request(api("/srt"));
        return res.ok ? parseSrt(await res.text()) : [];
      } catch (err) { return []; }
    }

    // The look's max_words is what "this line is too long" is measured
    // against, so it is re-read after every restyle.
    async function loadLook() {
      const job = getJob ? getJob() : null;
      const preset = job && job.options ? job.options.preset : "";
      const look = (await fetchJson("/api/styles") || []).find((s) => s.name === preset);
      const layout = look && look.definition ? look.definition.layout || {} : {};
      state.maxWords = layout.max_words || DEFAULT_MAX_WORDS;
      state.lookName = preset || "";
    }

    function adopt(t) {
      state.words = t.words || [];
      state.meta = t.meta || null;
      state.revision = t.revision || 0;
    }
    async function reload() {
      const transcript = await fetchJson(api("/transcript"));
      if (!transcript) return;
      adopt(transcript);
      state.cues = await fetchCues();
      render();
    }

    // ---- rendering ----

    function wordSpan(i) {
      const word = state.words[i];
      const m = (state.meta && state.meta[i]) || null;
      const span = document.createElement("span");
      const marks = [classify(word.p)];
      if (m && m.edited) marks.push("is-edited");
      if (m && m.retimed) marks.push("is-retimed");
      if (m && m.style) marks.push("is-styled");
      span.className = ["tw"].concat(marks.filter(Boolean)).join(" ");
      span.dataset.i = String(i);
      span.innerHTML = '<span class="tw-grip tw-left" title="Drag to move when this word starts"></span>'
        + '<button type="button" class="tw-text"></button><span class="tw-grip tw-right"'
        + ' title="Drag to move when this word ends"></span>';
      const text = span.querySelector(".tw-text");
      text.textContent = word.w;
      text.addEventListener("click", (e) => { e.stopPropagation(); select(i); });
      span.querySelectorAll(".tw-grip").forEach((grip) => {
        const edge = grip.classList.contains("tw-left") ? "start" : "end";
        grip.addEventListener("pointerdown", (e) => startDrag(e, i, edge, grip));
      });
      return span;
    }

    function render() {
      state.lines = assignToLines(state.words, state.cues);
      list.innerHTML = "";
      state.spans = [];
      for (const line of state.lines) {
        const row = document.createElement("div");
        row.className = "tedit-line";
        const words = document.createElement("div");
        words.className = "tedit-words";
        line.forEach((i, k) => {
          state.spans[i] = wordSpan(i);
          if (k > 0) words.appendChild(document.createTextNode(" "));
          words.appendChild(state.spans[i]);
        });
        row.appendChild(words);
        const warning = tooLongWarning(line, state.maxWords, state.lookName);
        if (warning) {
          const warn = document.createElement("p");
          warn.className = "tedit-warn";
          warn.textContent = warning;
          row.appendChild(warn);
        }
        list.appendChild(row);
      }
      section.hidden = state.words.length === 0;
      if (state.selected >= 0 && state.spans[state.selected]) placePopup(state.selected);
      else closePopup();
    }

    // ---- the popup ----

    function lineOf(index) { return state.lines.find((line) => line.indexOf(index) >= 0) || []; }
    function select(index) {
      state.selected = index;
      const word = state.words[index];
      input.value = word.w;
      const same = occurrences(state.words, index);
      act("all").textContent = `Fix every “${splitWordText(word.w)[1] || word.w}” (${same.length})`;
      act("all").hidden = same.length < 2;
      const first = lineOf(index)[0] === index;
      act("split").hidden = index === 0 || first;
      act("merge").hidden = index === 0 || !first;
      pop.querySelector(".tedit-times").textContent = `${formatSeconds(word.s)} – ${formatSeconds(word.e)}`;
      pop.hidden = false;
      placePopup(index);
      input.focus();
      input.select();
      clearOpen();
      if (state.spans[index]) state.spans[index].classList.add("is-open");
      if (player) player.seek(word.s);
      if (window.AshStudioWord && typeof AshStudioWord.select === "function") AshStudioWord.select(index);
    }

    function placePopup(index) {
      const span = state.spans[index];
      if (!span) return;
      const box = span.getBoundingClientRect();
      const width = pop.offsetWidth || 280;
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, box.left + window.scrollX));
      pop.style.left = `${Math.round(left)}px`;
      pop.style.top = `${Math.round(box.bottom + window.scrollY + 6)}px`;
    }

    function closePopup() {
      pop.hidden = true;
      state.selected = -1;
      clearOpen();
      if (window.AshStudioWord && typeof AshStudioWord.clear === "function") AshStudioWord.clear();
    }
    function clearOpen() { for (const s of state.spans) if (s) s.classList.remove("is-open"); }

    // ---- saving ----

    async function adoptConflict(body) {
      adopt(body.transcript);
      state.cues = await fetchCues();
      render();
      AshToast.show(body.detail, { kind: "bad", ms: 10000 });
      setState("");
    }
    async function patch(ops) {
      if (state.busy) return null;
      state.busy = true;
      section.classList.add("busy");
      setState("Saving…", "busy");
      try {
        const res = await AshApi.request(api("/transcript"), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: state.revision, ops }),
        });
        if (res.status === 409) {
          const body = await res.json().catch(() => ({}));
          if (body && body.transcript) return await adoptConflict(body), null;
          throw new Error((body && body.detail) || "Couldn't save that change");
        }
        if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't save that change"));
        const body = await res.json();
        adopt(body);
        state.cues = await fetchCues();
        render();
        if (player && assUrl) player.setTrack(assUrl()); // the video keeps playing
        setState("Saved", "ok");
        setTimeout(() => { if (stateLabel.textContent === "Saved") setState(""); }, 2500);
        notify(ops[0] ? ops[0].index : -1);
        return body;
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
        setState("");
        return null;
      } finally {
        state.busy = false;
        section.classList.remove("busy");
      }
    }

    async function commitText(all) {
      const index = state.selected;
      if (index < 0) return;
      const text = input.value.trim();
      closePopup();
      if (!text || text === state.words[index].w) return;
      await patch([{ op: "set_text", index, text, all: Boolean(all) }]);
    }

    async function teachGlossary() {
      const index = state.selected;
      if (index < 0) return;
      const from = splitWordText(state.words[index].w)[1];
      const to = splitWordText(input.value.trim())[1];
      if (!from || !to || from === to) {
        AshToast.show("Type the right spelling first, then teach it.", { kind: "bad" });
        return;
      }
      try {
        const res = await AshApi.request(api("/glossary"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from, to }),
        });
        if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't add it to the glossary"));
        const body = await res.json();
        const where = body.client ? `${body.client}'s glossary` : "the shared glossary";
        const said = body.added
          ? `Added “${body.line}” to ${where}. The next job will spell it that way.`
          : `“${body.line}” was already in ${where}.`;
        AshToast.show(said, { kind: "ok", ms: 8000 });
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
      }
    }

    // ---- dragging an edge ----

    function startDrag(e, index, edge, grip) {
      if (state.busy || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const from = edge === "start" ? state.words[index].s : state.words[index].e;
      const at = (ev) => {
        const seconds = from + (ev.clientX - e.clientX) * SECONDS_PER_PIXEL;
        return clampRetime(state.words, index, edge === "start" ? { start: seconds } : { end: seconds });
      };
      let moved = false;
      grip.setPointerCapture(e.pointerId);
      grip.classList.add("dragging");
      const move = (ev) => {
        moved = true;
        const next = at(ev);
        setState(`${formatSeconds(next.start)} – ${formatSeconds(next.end)}`, "busy");
      };
      const finish = (ev, cancelled) => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        grip.removeEventListener("pointercancel", cancel);
        grip.classList.remove("dragging");
        try { grip.releasePointerCapture(ev.pointerId); } catch (err) { /* already released */ }
        setState("");
        if (!moved || cancelled) return;
        const next = at(ev);
        patch([{ op: "retime", index, start: next.start, end: next.end }]);
      };
      const up = (ev) => finish(ev, false), cancel = (ev) => finish(ev, true);
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
      grip.addEventListener("pointercancel", cancel);
    }

    // ---- wiring ----

    pop.addEventListener("click", (e) => {
      const name = e.target && e.target.dataset ? e.target.dataset.act : null;
      const index = state.selected;
      if (name === "one") commitText(false);
      else if (name === "all") commitText(true);
      else if (name === "glossary") teachGlossary();
      else if (name === "split") { closePopup(); patch([{ op: "split", index }]); }
      else if (name === "merge") { closePopup(); patch([{ op: "merge", index }]); }
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commitText(false); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePopup(); }
    });
    document.addEventListener("pointerdown", (e) => {
      if (pop.hidden || pop.contains(e.target) || (e.target.closest && e.target.closest(".tw"))) return;
      closePopup();
    });
    window.addEventListener("resize", () => { if (state.selected >= 0) placePopup(state.selected); });

    (async () => { await loadLook(); await reload(); })();
    return { reload, refreshLook: async () => { await loadLook(); render(); } };
  }

  // ---- boot ----
  // studio.js fires onReady once the player and the job are there, and
  // onRestyled after every look change. None of this runs under node.

  let panel = null;

  if (typeof window !== "undefined" && typeof document !== "undefined") {
    const hooks = (window.AshStudio = window.AshStudio || {});
    hooks.onReady = hooks.onReady || [];
    hooks.onRestyled = hooks.onRestyled || [];
    hooks.onReady.push((context) => {
      panel = mount({
        jobId: decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || ""),
        player: context.player,
        getJob: context.getJob,
        assUrl: context.assUrl,
      });
    });
    hooks.onRestyled.push(async () => { if (panel) await panel.refreshLook(); });
  }

  function onWordEdited(index) { if (panel) panel.reload(); notify(index); }
  onWordEdited.subscribe = (fn) => { if (typeof fn === "function") listeners.push(fn); };

  const exported = {
    MIN_WORD_SECONDS, UNSURE, BAD, parseSrt, lineIndexFor, assignToLines, splitWordText,
    applyCase, occurrences, clampRetime, classify, tooLongWarning, formatSeconds, mount,
    onWordEdited, subscribe: onWordEdited.subscribe,
    reload: () => (panel ? panel.reload() : Promise.resolve()),
  };
  if (typeof window !== "undefined") window.AshStudioEdit = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})();
