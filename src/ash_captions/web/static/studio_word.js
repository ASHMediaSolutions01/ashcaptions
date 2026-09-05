/* Style one word (v0.6 design, section 2): click a word and give it its own
   colour, size, weight or slant without leaving the Studio and without
   changing the look.

   Every control is pre-filled with what the look already gives that word, so
   the toolbar reads as a diff rather than a blank form, and only the
   properties that actually differ are sent -- bold a word and its colour
   still follows the look. Above the controls one line names the blast
   radius, because the scope of an edit should be visible at the moment of
   editing rather than be a setting on another page.

   Deliberately not here: font family and outline. Those stay properties of
   the look; the combinatorial surface is where per-word styling turns into a
   mess.

   Persisting rides track A's PATCH /api/jobs/{id}/transcript as a
   `set_style` op -- one endpoint owns the transcript and one revision
   counter guards it. This file adds no route.

   The helpers above mount() are pure and exported for node, so
   tests/test_web/test_studio_word.py runs them (and the PATCH, against a
   fake fetch) without a browser; nothing here touches the DOM until
   AshStudio.onReady fires. */
(function () {
  "use strict";

  const STYLE_PAGE = "/style-editor";
  const MIN_PERCENT = 50; // WordStyle.scale is bounded 0.5-3.0 in styles/schema.py
  const MAX_PERCENT = 300;
  const LOOK_FALLBACK = { colors: {}, size: 72 };

  // ---- pure helpers (no DOM, no network) ----

  // "#RRGGBBAA" -> "#RRGGBB": <input type="color"> takes six digits, and the
  // per-word override is about hue, never the look's own transparency.
  function opaqueHex(value, fallback) {
    if (typeof value !== "string") return fallback;
    const m = /^#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?$/.exec(value.trim());
    return m ? `#${m[1].toUpperCase()}` : fallback;
  }

  function clampPercent(value) {
    const n = Math.round(Number(value));
    if (!Number.isFinite(n)) return 100;
    return Math.max(MIN_PERCENT, Math.min(MAX_PERCENT, n));
  }

  // What the look alone gives any word of a caption: its text colour, full
  // size, and the weight and slant of the Style line, which is always
  // regular (see styles/ass_format.py).
  function lookBaseline(look) {
    const colors = (look && look.colors) || {};
    return { colour: opaqueHex(colors.text, "#FFFFFF"), percent: 100, bold: false, italic: false };
  }

  // What this word renders as today: the look, with the word's own override
  // laid over it. This is what the controls are filled from.
  function effectiveStyle(word, look) {
    const base = lookBaseline(look);
    const own = (word && word.style) || {};
    return {
      colour: opaqueHex(own.colour, base.colour),
      percent: own.scale == null ? base.percent : clampPercent(own.scale * 100),
      bold: own.bold == null ? base.bold : !!own.bold,
      italic: own.italic == null ? base.italic : !!own.italic,
    };
  }

  function hasOverride(word) {
    const own = word && word.style;
    if (!own) return false;
    return ["colour", "scale", "bold", "italic", "x", "y"].some((k) => own[k] != null);
  }

  // Only what differs from the look is sent: a word that was merely bolded
  // keeps following the look's colour when the look changes. An empty diff
  // is null -- no override at all, not an empty one.
  function diffStyle(chosen, look, keep) {
    const base = lookBaseline(look);
    const style = {};
    if (opaqueHex(chosen.colour, base.colour) !== base.colour) style.colour = opaqueHex(chosen.colour, base.colour);
    const percent = clampPercent(chosen.percent);
    if (percent !== base.percent) style.scale = Math.round(percent) / 100;
    if (!!chosen.bold !== base.bold) style.bold = !!chosen.bold;
    if (!!chosen.italic !== base.italic) style.italic = !!chosen.italic;
    // Free placement is track F's; this toolbar never sets it, but it must
    // not drop it either when it rewrites a word that already has one.
    const previous = (keep && keep.style) || {};
    if (previous.x != null) style.x = previous.x;
    if (previous.y != null) style.y = previous.y;
    return Object.keys(style).length ? style : null;
  }

  // The documented body of PATCH /api/jobs/{id}/transcript (spec Interfaces).
  function patchBody(revision, index, style) {
    return { revision: revision, ops: [{ op: "set_style", index: index, style: style }] };
  }

  function resetAllOps(words) {
    const ops = [];
    (words || []).forEach((word, index) => {
      if (hasOverride(word)) ops.push({ op: "set_style", index: index, style: null });
    });
    return ops;
  }

  function wordIndexAt(words, t) {
    for (let i = 0; i < (words || []).length; i += 1) {
      if (t >= words[i].s && t < words[i].e) return i;
      if (words[i].s > t) return -1;
    }
    return -1;
  }

  async function detailOf(res, fallback) {
    if (typeof AshApi !== "undefined" && AshApi.errorDetail) return AshApi.errorDetail(res, fallback);
    return fallback;
  }

  // One PATCH. `request` is injected so a test can hand in a fake fetch; the
  // page passes AshApi.request, which carries the X-ASH-Client header every
  // mutating route requires.
  async function sendOps(options) {
    const { request, jobId, revision, ops } = options;
    const res = await request(`/api/jobs/${encodeURIComponent(jobId)}/transcript`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: revision, ops: ops }),
    });
    if (res.status === 409) {
      // Another tab moved first. Never clobber: hand back what the server
      // has so the caller can reload rather than retry blindly.
      let current = null;
      try { current = await res.json(); } catch (err) { current = null; }
      return { conflict: true, current: current };
    }
    if (!res.ok) throw new Error(await detailOf(res, "Couldn't style that word"));
    return { conflict: false, result: await res.json() };
  }

  function saveStyle(options) {
    return sendOps({
      request: options.request,
      jobId: options.jobId,
      revision: options.revision,
      ops: patchBody(options.revision, options.index, options.style).ops,
    });
  }

  // ---- the toolbar ----

  let panel = null; // set by mount(); select()/clear() are no-ops before it

  function button(label, title, className) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = className;
    el.textContent = label;
    el.title = title;
    return el;
  }

  function buildSkeleton(root) {
    root.innerHTML = "";
    root.hidden = true;

    const scope = document.createElement("p");
    scope.className = "word-scope";
    const strong = document.createElement("strong");
    strong.textContent = "this word";
    const link = document.createElement("a");
    link.href = STYLE_PAGE;
    link.textContent = "Change the look instead →";
    scope.append(document.createTextNode("Changing "), strong, document.createTextNode(" only. "), link);

    const controls = document.createElement("div");
    controls.className = "word-controls";

    const which = document.createElement("span");
    which.className = "word-which";

    const colourLabel = document.createElement("label");
    colourLabel.className = "word-field";
    const colour = document.createElement("input");
    colour.type = "color";
    colour.id = "word-colour";
    colourLabel.append(document.createTextNode("Colour "), colour);

    const sizeLabel = document.createElement("label");
    sizeLabel.className = "word-field";
    const size = document.createElement("input");
    size.type = "number";
    size.id = "word-size";
    size.min = String(MIN_PERCENT);
    size.max = String(MAX_PERCENT);
    size.step = "5";
    sizeLabel.append(document.createTextNode("Size "), size, document.createTextNode(" %"));

    const bold = button("B", "Bold this word", "word-toggle word-bold");
    const italic = button("I", "Italic this word", "word-toggle word-italic");
    const resetWord = button("Reset word", "Put this word back to the look", "btn small");
    const resetAll = button("Reset all overrides on this job", "Put every word back to the look", "btn small");
    const close = button("Close", "Close the toolbar (Escape)", "btn small word-close");

    controls.append(which, colourLabel, sizeLabel, bold, italic, resetWord, resetAll, close);
    root.append(scope, controls);
    return { colour, size, bold, italic, resetWord, resetAll, close, which };
  }

  // The transcript's word elements, in transcript order. Track A's panel
  // marks them with data-word-index; until it lands, the caption-check
  // panel's own spans are in exactly that order.
  function wordElements() {
    const marked = document.querySelectorAll("[data-word-index]");
    if (marked.length) return Array.from(marked);
    return Array.from(document.querySelectorAll("#check .check-src .w"));
  }

  function indexOfElement(el) {
    if (el.dataset && el.dataset.wordIndex != null) return Number(el.dataset.wordIndex);
    return wordElements().indexOf(el);
  }

  function mount(refs) {
    const { jobId, player, live, getJob, assUrl, request } = refs;
    const root = document.getElementById("word-toolbar");
    if (!root || !player) return null;
    const els = buildSkeleton(root);
    const state = { words: [], revision: 0, index: -1, looks: {}, busy: false, downAt: null };

    function look() {
      const job = getJob();
      return state.looks[job && job.options && job.options.preset] || LOOK_FALLBACK;
    }

    function currentWord() {
      return state.index >= 0 ? state.words[state.index] : null;
    }

    function render() {
      const word = currentWord();
      if (!word) {
        root.hidden = true;
        return;
      }
      const shown = effectiveStyle(word, look());
      els.colour.value = shown.colour;
      els.size.value = String(shown.percent);
      els.bold.setAttribute("aria-pressed", String(shown.bold));
      els.italic.setAttribute("aria-pressed", String(shown.italic));
      els.bold.classList.toggle("on", shown.bold);
      els.italic.classList.toggle("on", shown.italic);
      els.which.textContent = `“${word.w}”`;
      els.which.classList.toggle("has-override", hasOverride(word));
      els.resetWord.disabled = !hasOverride(word);
      els.resetAll.disabled = resetAllOps(state.words).length === 0;
      root.hidden = false;
      markOverrides();
    }

    // The dot: a word carrying an override says so before the confusion
    // rather than after it.
    function markOverrides() {
      const elements = wordElements();
      elements.forEach((el, i) => {
        el.classList.toggle("word-override", hasOverride(state.words[i]));
        if (i === state.index) el.classList.add("word-selected");
        else el.classList.remove("word-selected");
      });
    }

    function controlValues() {
      return {
        colour: els.colour.value,
        percent: els.size.value,
        bold: els.bold.getAttribute("aria-pressed") === "true",
        italic: els.italic.getAttribute("aria-pressed") === "true",
      };
    }

    async function commit(ops) {
      if (state.busy || !ops.length) return;
      state.busy = true;
      root.classList.add("busy");
      try {
        const outcome = await sendOps({ request: request, jobId: jobId, revision: state.revision, ops: ops });
        if (outcome.conflict) {
          AshToast.show("Another tab changed this transcript. Reloading it.", { kind: "bad" });
          await load();
          return;
        }
        // The server re-renders the .ass; reload the track in place so the
        // video keeps playing and only the captions change.
        applyOps(ops);
        if (outcome.result && outcome.result.revision != null) state.revision = outcome.result.revision;
        if (live && assUrl) player.setTrack(assUrl());
        render();
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
      } finally {
        state.busy = false;
        root.classList.remove("busy");
      }
    }

    function applyOps(ops) {
      for (const op of ops) {
        const word = state.words[op.index];
        if (!word) continue;
        if (op.style === null) delete word.style;
        else word.style = op.style;
      }
    }

    function changed() {
      const word = currentWord();
      if (!word) return;
      commit([{ op: "set_style", index: state.index, style: diffStyle(controlValues(), look(), word) }]);
    }

    function toggle(el) {
      el.setAttribute("aria-pressed", el.getAttribute("aria-pressed") === "true" ? "false" : "true");
      changed();
    }

    function select(index) {
      if (!(index >= 0) || index >= state.words.length) return clear();
      state.index = index;
      render();
      return undefined;
    }

    function clear() {
      state.index = -1;
      root.hidden = true;
      markOverrides();
    }

    async function load() {
      try {
        const res = await request(`/api/jobs/${encodeURIComponent(jobId)}/transcript`);
        if (!res.ok) return;
        const transcript = await res.json();
        state.words = transcript.words || [];
        state.revision = transcript.revision || 0;
      } catch (err) {
        state.words = [];
      }
      if (state.index >= state.words.length) state.index = -1;
      render();
      markOverrides();
    }

    async function loadLooks() {
      try {
        const res = await request("/api/styles");
        if (!res.ok) return;
        state.looks = {};
        for (const s of await res.json()) state.looks[s.name] = s.definition || LOOK_FALLBACK;
      } catch (err) {
        /* an unknown look falls back to white text at 100% */
      }
    }

    els.colour.addEventListener("change", changed);
    els.size.addEventListener("change", changed);
    els.bold.addEventListener("click", () => toggle(els.bold));
    els.italic.addEventListener("click", () => toggle(els.italic));
    els.resetWord.addEventListener("click", () => commit([{ op: "set_style", index: state.index, style: null }]));
    els.resetAll.addEventListener("click", () => commit(resetAllOps(state.words)));
    els.close.addEventListener("click", clear);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !root.hidden) clear();
    });

    // A word in the transcript. Capture phase, because the caption-check
    // panel stops the click bubbling once it has seeked to the word.
    document.addEventListener(
      "click",
      (e) => {
        const el = e.target.closest && e.target.closest("[data-word-index], #check .check-src .w");
        if (!el) return;
        const index = indexOfElement(el);
        if (index >= 0) select(index);
      },
      true
    );

    // A word on the video. The caption is a canvas, so the word we can name
    // is the one on screen at the playhead; studio_drag's handle covers the
    // caption box and a click that did not move is a no-op for it.
    const handle = document.getElementById("caption-handle");
    if (handle) {
      handle.addEventListener("pointerdown", (e) => { state.downAt = { x: e.clientX, y: e.clientY }; });
      handle.addEventListener("click", (e) => {
        const from = state.downAt;
        state.downAt = null;
        if (from && Math.hypot(e.clientX - from.x, e.clientY - from.y) > 4) return; // that was a drag
        const index = wordIndexAt(state.words, player.currentTime);
        if (index >= 0) select(index);
      });
    }

    // Track A's transcript panel, when it is there, can announce a click
    // either by pushing onto its own hooks array or by dispatching this
    // event. Neither existing is fine: select() is the documented entry.
    const edit = window.AshStudioEdit;
    if (edit && Array.isArray(edit.onWordClicked)) edit.onWordClicked.push(select);
    document.addEventListener("ash-word-click", (e) => {
      if (e.detail && e.detail.index != null) select(Number(e.detail.index));
    });

    (async () => {
      await loadLooks();
      await load();
    })();

    return { select, clear, reload: load, markOverrides };
  }

  const exported = {
    MIN_PERCENT, MAX_PERCENT,
    opaqueHex, clampPercent, lookBaseline, effectiveStyle, hasOverride, diffStyle,
    patchBody, resetAllOps, wordIndexAt, sendOps, saveStyle, mount,
    select: (index) => (panel ? panel.select(index) : undefined),
    clear: () => (panel ? panel.clear() : undefined),
  };

  if (typeof window !== "undefined") window.AshStudioWord = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;

  if (typeof document !== "undefined") {
    // The stylesheet loads itself, so this track's edit to studio.html stays
    // the two lines it owns: one script tag and one empty mount element.
    const self = document.currentScript;
    const version = self && self.src.includes("?") ? self.src.slice(self.src.indexOf("?")) : "";
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `/static/studio_word.css${version}`;
    document.head.appendChild(link);

    const hooks = (window.AshStudio = window.AshStudio || {});
    hooks.onReady = hooks.onReady || [];
    hooks.onRestyled = hooks.onRestyled || [];
    hooks.onReady.push((context) => {
      panel = mount({
        jobId: context.getJob().id,
        player: context.player,
        live: context.live,
        getJob: context.getJob,
        assUrl: context.assUrl,
        request: AshApi.request,
      });
    });
    hooks.onRestyled.push(() => { if (panel) panel.reload(); });
  }
})();
