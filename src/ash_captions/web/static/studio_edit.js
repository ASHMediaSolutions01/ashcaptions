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

  // The rules live in studio_edit_rules.js (this file is at the line
  // ceiling); under node they are required, in the page they are global.
  const R = typeof window !== "undefined" && window.AshStudioEditRules
    ? window.AshStudioEditRules
    : require("./studio_edit_rules.js");
  const {
    MIN_WORD_SECONDS, DEFAULT_MAX_WORDS, UNSURE, BAD, SECONDS_PER_PIXEL, parseSrt, lineIndexFor,
    assignToLines, splitWordText, applyCase, occurrences, clampRetime, classify, tooLongWarning,
    formatSeconds, lineSummary, nudgeRetime,
  } = R;

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
    '<span class="tedit-hint">Click a word to fix it. Drag its edges to change when it lands — or Tab to a word, press [ or ] for an edge, then ← →.</span>',
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
        // A grip is a slider to the keyboard: reached with [ or ] from its
        // word, nudged with the arrows (Shift for a bigger step).
        const at = edge === "start" ? word.s : word.e;
        grip.setAttribute("role", "slider");
        grip.tabIndex = -1;
        grip.setAttribute("aria-label", `${edge === "start" ? "Start" : "End"} of “${word.w}”`);
        grip.setAttribute("aria-valuenow", String(at));
        grip.setAttribute("aria-valuetext", formatSeconds(at));
        grip.addEventListener("keydown", (e) => gripKey(e, i, edge));
      });
      return span;
    }

    // One Tab stop for the whole transcript: the current word takes the
    // focus, the arrows move it. Hundreds of words were hundreds of stops.
    function rove(index) {
      for (const span of state.spans) {
        if (span) span.querySelector(".tw-text").tabIndex = -1;
      }
      const span = state.spans[index];
      if (span) span.querySelector(".tw-text").tabIndex = 0;
    }

    function gripKey(e, index, edge) {
      const step = e.shiftKey ? 0.25 : 0.05;
      const delta = e.key === "ArrowRight" ? step : e.key === "ArrowLeft" ? -step : 0;
      if (e.key === "Escape") { e.preventDefault(); state.spans[index].querySelector(".tw-text").focus(); return; }
      if (!delta || state.busy) return;
      e.preventDefault();
      e.stopPropagation();
      const next = nudgeRetime(state.words, index, edge, delta);
      const w = state.words[index];
      if (next.start === w.s && next.end === w.e) return; // clamped against a neighbour: nothing to save
      Promise.resolve(patch([{ op: "retime", index, start: next.start, end: next.end }])).then(() => {
        const grip = state.spans[index] && state.spans[index].querySelector(edge === "start" ? ".tw-left" : ".tw-right");
        if (grip) grip.focus();
      });
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
        // A line the look will split is marked, not lectured: the summary
        // at the top says how many, the mark says which.
        const warning = tooLongWarning(line, state.maxWords, state.lookName);
        if (warning) { row.classList.add("is-split"); row.title = warning; }
        list.appendChild(row);
      }
      const summary = lineSummary(state.lines, state.maxWords, state.lookName);
      if (summary) {
        const note = document.createElement("p");
        note.className = "tedit-summary";
        note.textContent = summary;
        list.prepend(note);
      }
      rove(state.selected >= 0 ? state.selected : (state.lines[0] || [])[0]);
      section.hidden = state.words.length === 0;
      if (state.selected >= 0 && state.spans[state.selected]) placePopup(state.selected);
      else closePopup(true); // a re-draw dismisses nothing: keep the toolbar
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
      placePopup(index, true);
      input.focus();
      input.select();
      clearOpen();
      if (state.spans[index]) state.spans[index].classList.add("is-open");
      if (player) player.seek(word.s);
      if (window.AshStudioWord && typeof AshStudioWord.select === "function") AshStudioWord.select(index);
    }

    // Geometry: studio_edit_pop.js. `settle` re-places it after the reflow.
    function placePopup(index, settle) {
      const span = state.spans[index];
      if (!span || !window.AshEditPopup) return;
      AshEditPopup.place(pop, span);
      if (settle) requestAnimationFrame(() => { if (state.selected === index) placePopup(index); });
    }

    function closePopup(keepToolbar) {
      pop.hidden = true;
      state.selected = -1;
      clearOpen();
      if (!keepToolbar && window.AshStudioWord && AshStudioWord.clear) AshStudioWord.clear();
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
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic event */ }
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
    list.addEventListener("keydown", (e) => {
      const btn = e.target && e.target.closest ? e.target.closest(".tw-text") : null;
      if (!btn) return;
      const index = Number(btn.parentElement.dataset.i);
      if (e.key === "[" || e.key === "]") {
        e.preventDefault();
        state.spans[index].querySelector(e.key === "[" ? ".tw-left" : ".tw-right").focus();
        return;
      }
      const order = state.lines.flat();
      const at = order.indexOf(index);
      const to = e.key === "ArrowRight" ? order[at + 1] : e.key === "ArrowLeft" ? order[at - 1]
        : e.key === "Home" ? order[0] : e.key === "End" ? order[order.length - 1] : undefined;
      if (to == null) return;
      e.preventDefault();
      rove(to);
      state.spans[to].querySelector(".tw-text").focus();
    });
    document.addEventListener("pointerdown", (e) => {
      if (pop.hidden || pop.contains(e.target) || (e.target.closest && e.target.closest(".tw"))) return;
      closePopup(Boolean(e.target.closest && e.target.closest("#word-toolbar"))); // keep the toolbar
    });
    if (window.AshEditPopup) {
      AshEditPopup.follow(pop, () => (state.selected >= 0 ? state.spans[state.selected] : null),
        [document.getElementById("edit-column"), list]);
    }

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
    applyCase, occurrences, clampRetime, classify, tooLongWarning, formatSeconds, lineSummary,
    nudgeRetime, mount,
    onWordEdited, subscribe: onWordEdited.subscribe,
    reload: () => (panel ? panel.reload() : Promise.resolve()),
  };
  if (typeof window !== "undefined") window.AshStudioEdit = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})();
