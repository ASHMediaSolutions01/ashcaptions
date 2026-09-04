/* Caption position (v0.5 §1): drag the caption anywhere on the video.
   A transparent layer over the JASSUB canvas carries one handle sized to
   the caption's box. Dragging moves the dashed outline only; release
   restyles once with the new anchor (fractions of the frame, stored per
   job) and the track reloads in place without touching the playhead.
   Arrow keys nudge 1% (Shift: 5%), Escape and the Reset button clear it.
   studio.js hands over the player and the job through AshStudio.onReady
   and reports every look change through AshStudio.onRestyled. */
(function () {
  "use strict";

  const hooks = (window.AshStudio = window.AshStudio || {});
  hooks.onReady = hooks.onReady || [];
  hooks.onRestyled = hooks.onRestyled || [];

  const $ = (id) => document.getElementById(id);
  const layer = $("caption-drag");
  const handle = $("caption-handle");
  const resetBtn = $("reset-position-btn");
  const frame = $("frame");
  const video = $("video");
  if (!layer || !handle || !resetBtn || !frame || !video) return;

  const NUDGE = 0.01;
  const BIG_NUDGE = 0.05;
  const BOX_WIDTH = 0.7; // of the frame: a caption-shaped box, not the exact glyph outline

  let ctx = null; // { player, live, api, assUrl, getJob, setJob } from studio.js
  let looks = {}; // look name -> { layout, size } from GET /api/styles
  let drag = null; // { pointerId, startX, startY, fromX, fromY, moved } while dragging
  let busy = false;

  // ---- geometry: the JS twin of render._anchor_xy, in fractions of the frame ----

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function round3(v) { return Math.round(v * 1000) / 1000; }

  function currentLook() {
    return looks[ctx.getJob().options.preset] || { layout: {}, size: 72 };
  }

  // Where the look itself puts the anchor: the same rule as the renderer
  // (bottom/lower third: height - margin_v; top: margin_v; centre: half).
  function defaultAnchor() {
    const { layout } = currentLook();
    const w = video.videoWidth || 1080;
    const h = video.videoHeight || 1920;
    const position = layout.position || "bottom";
    const align = layout.align || "center";
    const mv = layout.margin_v == null ? 120 : layout.margin_v;
    const ml = layout.margin_l == null ? 80 : layout.margin_l;
    const mr = layout.margin_r == null ? 80 : layout.margin_r;
    const y = position === "top" ? mv / h : position === "center" ? 0.5 : (h - mv) / h;
    const x = align === "left" ? ml / w : align === "right" ? (w - mr) / w : 0.5;
    return { x, y };
  }

  function storedAnchor() {
    const o = ctx.getJob().options;
    return o.caption_x != null && o.caption_y != null ? { x: o.caption_x, y: o.caption_y } : null;
  }

  function anchor() { return storedAnchor() || defaultAnchor(); }

  // Places the handle so the anchor sits where libass puts it: the box's
  // bottom edge for bottom looks, its centre for centre, its top edge for
  // top; left, centre or right edge across.
  function place(a) {
    const { layout, size } = currentLook();
    const fw = frame.clientWidth;
    const fh = frame.clientHeight;
    const boxH = Math.max(28, (size / (video.videoHeight || 1920)) * fh * 1.5);
    const boxW = fw * BOX_WIDTH;
    const position = layout.position || "bottom";
    const align = layout.align || "center";
    const top = position === "top" ? a.y * fh : position === "center" ? a.y * fh - boxH / 2 : a.y * fh - boxH;
    const left = align === "left" ? a.x * fw : align === "right" ? a.x * fw - boxW : a.x * fw - boxW / 2;
    handle.style.width = `${Math.round(boxW)}px`;
    handle.style.height = `${Math.round(boxH)}px`;
    handle.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  function describe(a) {
    return `${Math.round(a.x * 100)}% across, ${Math.round(a.y * 100)}% down`;
  }

  function refresh() {
    if (!ctx || !ctx.live) { layer.hidden = true; resetBtn.hidden = true; return; }
    layer.hidden = false;
    place(anchor());
    resetBtn.hidden = storedAnchor() === null;
    handle.setAttribute("aria-valuetext", describe(anchor()));
  }

  // ---- one restyle per release: the same look, at the new position ----

  async function commit(position) {
    if (busy || !ctx) return;
    busy = true;
    handle.classList.add("busy");
    try {
      const job = ctx.getJob();
      const body = {
        preset: job.options.preset,
        caption_x: position ? position.x : null,
        caption_y: position ? position.y : null,
      };
      const res = await AshApi.request(ctx.api("/restyle"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't move the caption"));
      ctx.setJob(await res.json());
      ctx.player.setTrack(ctx.assUrl()); // the video keeps playing; only the captions reload
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    } finally {
      busy = false;
      handle.classList.remove("busy");
      refresh();
    }
  }

  // ---- pointer drag: the outline follows the pointer, nothing re-renders until release ----

  function pointFrom(e) {
    const box = frame.getBoundingClientRect(); // the letterboxed video box, exactly
    return { x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height };
  }

  function dragTarget(e) {
    const at = pointFrom(e);
    return { x: clamp01(drag.fromX + (at.x - drag.startX)), y: clamp01(drag.fromY + (at.y - drag.startY)) };
  }

  handle.addEventListener("pointerdown", (e) => {
    if (busy || e.button !== 0) return;
    const from = anchor();
    const at = pointFrom(e);
    drag = { pointerId: e.pointerId, startX: at.x, startY: at.y, fromX: from.x, fromY: from.y, moved: false };
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    handle.focus();
    e.preventDefault();
  });

  handle.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    drag.moved = true;
    place(dragTarget(e));
  });

  function endDrag(e, cancelled) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const target = drag.moved && !cancelled ? dragTarget(e) : null;
    drag = null;
    handle.classList.remove("dragging");
    try { handle.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
    if (target) commit({ x: round3(target.x), y: round3(target.y) });
    else refresh();
  }
  handle.addEventListener("pointerup", (e) => endDrag(e, false));
  handle.addEventListener("pointercancel", (e) => endDrag(e, true));

  // ---- keyboard: arrows nudge, Escape resets ----

  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? BIG_NUDGE : NUDGE;
    const deltas = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (deltas[e.key]) {
      e.preventDefault();
      e.stopPropagation(); // studio.js walks the looks on arrow keys at document level
      const a = anchor();
      commit({ x: round3(clamp01(a.x + deltas[e.key][0])), y: round3(clamp01(a.y + deltas[e.key][1])) });
    } else if (e.key === "Escape" && storedAnchor()) {
      e.preventDefault();
      e.stopPropagation();
      commit(null);
    }
  });

  resetBtn.addEventListener("click", () => commit(null));

  // ---- the layout each look anchors from ----

  async function loadLooks() {
    try {
      const res = await AshApi.request("/api/styles");
      if (!res.ok) return;
      looks = {};
      for (const s of await res.json()) {
        const d = s.definition || {};
        looks[s.name] = { layout: d.layout || {}, size: d.size || 72 };
      }
    } catch (err) {
      /* keep what we had; an unknown look falls back to bottom-centre */
    }
  }

  hooks.onReady.push(async (context) => {
    ctx = context;
    await loadLooks();
    refresh();
    window.addEventListener("resize", refresh);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(refresh).observe(frame);
  });

  hooks.onRestyled.push(async (job) => {
    if (!ctx) return;
    if (!looks[job.options.preset]) await loadLooks(); // a look saved after the page loaded
    refresh();
  });
})();
