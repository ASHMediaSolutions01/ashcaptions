/* The shared "look card" preview (v0.6 spec 4): a small JASSUB canvas
   looping ~2 seconds of a style's own entrance / active-word / exit
   animation, instead of the static picture the Styles page and Studio
   used to show. One component, three consumers: the Styles page's style
   list, its header preview, and the Studio's looks list (studio_looks.js).

   The sample .ass itself is built by look_card_ass.js (loaded first, see
   style_editor.html / studio_looks.js's loader below); this file is the
   DOM half: the CSS poster fallback and a JASSUB pool.

   Cost control: JASSUB spins up a real Web Worker + WebAssembly instance
   per renderer, so this never creates one per card. A fixed-size pool of
   POOL_SIZE renderers is built once (lazily, on first use) and its
   canvases are physically moved between cards as they scroll in and out
   of view (IntersectionObserver), each move carrying only a `setTrack`
   call -- no new Worker. A card without a pool slot shows a static CSS
   poster (the same word-swatch markup style_editor.js and
   studio_looks.js used to build separately) instead of nothing. */
(function () {
  "use strict";

  const LOOP_MS = window.AshLookCardAss.LOOP_MS;
  const SAMPLE_WORDS = window.AshLookCardAss.SAMPLE_WORDS;
  const buildSampleAss = window.AshLookCardAss.buildSampleAss;
  const POOL_SIZE = 4;
  const VENDOR = "/static/vendor/jassub/";

  function buildPoster(style, opts) {
    const o = Object.assign({ fontDivisor: 3.8, fontMin: 15, fontMax: 24 }, opts);
    const colors = style.colors || {};
    const active = style.active_word || {};
    const poster = document.createElement("div");
    poster.className = "ash-look-poster";
    poster.style.fontFamily = `${JSON.stringify(style.font || "Inter")}, sans-serif`;
    poster.style.fontSize = `${Math.round(Math.min(o.fontMax, Math.max(o.fontMin, (style.size || 72) / o.fontDivisor)))}px`;
    poster.style.color = colors.text || "#fff";
    poster.style.letterSpacing = `${(style.letter_spacing || 0) * 0.02}em`;
    poster.style.textTransform = style.uppercase ? "uppercase" : "none";
    poster.style.textShadow = `0 0 2px ${colors.outline || "#000"}, 0 2px 3px ${colors.shadow || "transparent"}`;
    SAMPLE_WORDS.forEach((word, i) => {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = word;
      if (i === 1) {
        w.style.color = colors.active || colors.text || "#fff";
        const boxed = active.box || ["box", "scale_box", "card_box"].includes(active.effect);
        if (boxed && colors.box) w.style.background = colors.box;
        if (active.effect === "glow") w.style.textShadow = `0 0 8px ${colors.active || "#fff"}`;
        if (active.effect === "pop" || active.effect === "scale_box") w.style.transform = "scale(1.1)";
      }
      poster.appendChild(w);
    });
    return poster;
  }

  function jassubAvailable() {
    return typeof JASSUB !== "undefined" && typeof Worker !== "undefined" && typeof IntersectionObserver !== "undefined";
  }

  async function loadFontMap() {
    try {
      const req = window.AshApi ? window.AshApi.request : fetch;
      const res = await req("/api/fonts/files");
      if (!res.ok) return {};
      const files = await res.json();
      const map = {};
      for (const f of files) map[f.family.toLowerCase()] = f.url;
      return map;
    } catch (err) {
      return {};
    }
  }

  let poolPromise = null;
  let rafId = null;

  function resizeSlotCanvas(slot) {
    const el = slot.card;
    if (!el) return;
    const rect = el._ashLookStage.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    try {
      slot.renderer.resize(w, h);
    } catch (err) {
      /* transient -- the next resize (or the next assignment) fixes it */
    }
  }

  function startClock(pool) {
    if (rafId) return;
    const t0 = performance.now();
    const tick = () => {
      const t = (performance.now() - t0) % LOOP_MS;
      for (const slot of pool) {
        if (!slot.card) continue;
        try {
          slot.renderer.setCurrentTime(false, t / 1000);
        } catch (err) {
          /* a renderer mid-teardown -- next frame skips it cleanly */
        }
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    window.addEventListener("resize", () => {
      for (const slot of pool) if (slot.card) resizeSlotCanvas(slot);
    });
  }

  // A renderer built with no track at all (no `subContent`/`subUrl`)
  // leaves the worker's internal libass track object never created, and
  // a later `setTrack` on it fails outright ("Failed to start a track",
  // observed live against a real dev server -- see the plan's report).
  // An empty-but-valid script at construction time avoids that; the pool
  // never actually shows this track, only ever `setTrack`s over it.
  const EMPTY_TRACK =
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n" +
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, " +
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, " +
    "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\n" +
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n";

  function ensurePool() {
    if (poolPromise) return poolPromise;
    if (!jassubAvailable()) return Promise.resolve(null);
    poolPromise = loadFontMap().then((fontMap) => {
      const availableFonts = Object.assign({ "liberation sans": VENDOR + "default.woff2" }, fontMap);
      const pool = [];
      for (let i = 0; i < POOL_SIZE; i++) {
        const canvas = document.createElement("canvas");
        canvas.className = "ash-look-canvas";
        let renderer;
        try {
          renderer = new JASSUB({
            canvas,
            workerUrl: VENDOR + "jassub-worker.js",
            wasmUrl: VENDOR + "jassub-worker.wasm",
            modernWasmUrl: VENDOR + "jassub-worker-modern.wasm",
            fallbackFont: "liberation sans",
            availableFonts,
            useLocalFonts: false,
            subContent: EMPTY_TRACK,
          });
        } catch (err) {
          console.error("AshLookCard: JASSUB failed to start; cards fall back to static posters.", err);
          return null;
        }
        pool.push({ canvas, renderer, card: null, assignedAt: 0 });
      }
      startClock(pool);
      return pool;
    });
    return poolPromise;
  }

  function releaseSlotObj(slot) {
    const el = slot.card;
    if (el) {
      if (slot.canvas.parentNode) slot.canvas.parentNode.removeChild(slot.canvas);
      el.classList.remove("is-animated");
      delete el._ashLookSlot;
    }
    slot.card = null;
  }

  function releaseSlot(el) {
    if (el._ashLookSlot) releaseSlotObj(el._ashLookSlot);
  }

  async function assignSlot(el) {
    if (el._ashLookSlot) return;
    const pool = await ensurePool();
    if (!pool || !el._ashLookVisible || !el.isConnected) return; // unavailable, or scrolled away meanwhile
    let slot = pool.find((s) => !s.card);
    if (!slot) {
      // Every slot busy: steal the one assigned longest ago -- fine for a
      // decorative preview, and keeps the pool fixed-size regardless of
      // how many cards are simultaneously in view.
      slot = pool.reduce((oldest, s) => (s.assignedAt < oldest.assignedAt ? s : oldest), pool[0]);
      releaseSlotObj(slot);
    }
    let ass;
    try {
      ass = buildSampleAss(el._ashLookStyle);
    } catch (err) {
      console.error("AshLookCard: couldn't build a sample for", el._ashLookStyle && el._ashLookStyle.name, err);
      return;
    }
    slot.card = el;
    slot.assignedAt = performance.now();
    el._ashLookSlot = slot;
    el._ashLookStage.appendChild(slot.canvas);
    el.classList.add("is-animated");
    resizeSlotCanvas(slot);
    try {
      slot.renderer.setTrack(ass);
    } catch (err) {
      console.error("AshLookCard: setTrack failed", err);
      releaseSlot(el);
    }
  }

  let io = null;
  function observer() {
    if (io) return io;
    io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const el = entry.target;
          el._ashLookVisible = entry.isIntersecting;
          if (entry.isIntersecting) {
            assignSlot(el);
          } else {
            releaseSlot(el);
            if (!el.isConnected) io.unobserve(el);
          }
        }
      },
      { rootMargin: "100px" }
    );
    return io;
  }

  function create(style, posterOpts) {
    const el = document.createElement("div");
    el.className = "ash-look-card";
    const stage = document.createElement("div");
    stage.className = "ash-look-stage";
    const poster = buildPoster(style, posterOpts);
    stage.appendChild(poster);
    el.appendChild(stage);
    el._ashLookStyle = style;
    el._ashLookStage = stage;
    el._ashLookPosterOpts = posterOpts;
    el._ashLookVisible = false;
    observer().observe(el);
    return el;
  }

  // Refreshes an existing card in place -- for a caller (the style
  // editor's live form) that changes the same card's style repeatedly
  // and would rather not tear down its pool slot / observer registration
  // each time. Cheap either way: rebuilding the poster is plain DOM, and
  // an animated card's `setTrack` reuses its already-running renderer.
  function update(el, style) {
    if (!el || !el._ashLookStage) return;
    el._ashLookStyle = style;
    const stage = el._ashLookStage;
    const oldPoster = stage.querySelector(".ash-look-poster");
    const poster = buildPoster(style, el._ashLookPosterOpts);
    if (oldPoster) stage.replaceChild(poster, oldPoster);
    else stage.insertBefore(poster, stage.firstChild);
    if (el._ashLookSlot) {
      try {
        el._ashLookSlot.renderer.setTrack(buildSampleAss(style));
      } catch (err) {
        /* the next assignment (or the clock's next tick) recovers */
      }
    }
  }

  function dispose(el) {
    if (!el) return;
    el._ashLookVisible = false;
    try {
      observer().unobserve(el);
    } catch (err) {
      /* never observed, or already gone -- fine either way */
    }
    releaseSlot(el);
  }

  window.AshLookCard = { create, update, dispose };
})();
