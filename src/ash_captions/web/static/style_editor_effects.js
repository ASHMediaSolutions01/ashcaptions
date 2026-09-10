/* Box and shadow, on the Colours tab (v0.6 spec, held item 4).

   Its own file for the reason style_editor_sound.js is: style_editor.js
   is near the 500-line ceiling the tests enforce, and these controls are
   a self-contained group -- a colour, an opacity and some geometry for
   each of the two things drawn behind the words.

   Opacity is not a schema field. Every colour in a look is
   "#RRGGBB" or "#RRGGBBAA", so a look already carries the opacity of its
   box and its shadow; what was missing was any way to set it. These
   sliders write the alpha byte of colors.box / colors.shadow, which
   keeps one source of truth rather than adding a second that could
   disagree with it.

   ASS alpha is inverted (00 opaque, FF invisible) but that inversion
   belongs to the renderer -- ass_format does it. What is stored here is
   ordinary CSS-style alpha, FF opaque. */
(function (root) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // schema.py's defaults, so a look that says nothing still shows the
  // right thing on the sliders.
  const DEFAULTS = { padding: 0.28, angle: 45, distance: 2.83 };

  // ---- pure helpers (no DOM: exercised directly by the tests) ----

  function alphaOf(hex) {
    const value = String(hex || "");
    return value.length === 9 ? parseInt(value.slice(7), 16) : 255;
  }

  function withAlpha(hex, alpha255) {
    const base = String(hex || "#000000").slice(0, 7);
    const clamped = Math.max(0, Math.min(255, Math.round(alpha255)));
    // A fully opaque colour is written as plain #RRGGBB, which is what
    // the shipped looks use and what keeps a saved file readable.
    if (clamped >= 255) return base;
    return base + clamped.toString(16).toUpperCase().padStart(2, "0");
  }

  function percentToAlpha(percent) {
    return Math.round((Math.max(0, Math.min(100, Number(percent))) / 100) * 255);
  }

  function alphaToPercent(alpha255) {
    return Math.round((alpha255 / 255) * 100);
  }

  const api = { alphaOf, withAlpha, percentToAlpha, alphaToPercent, DEFAULTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AshStyleEffects = api;

  if (typeof document === "undefined") return;

  // ---- the controls ----

  let getDraft = () => null;
  let onChange = () => {};

  const els = {
    boxColour: $("colour-box"),
    boxOpacity: $("box-opacity"),
    boxOpacityOut: $("box-opacity-out"),
    boxPadding: $("box-padding"),
    boxPaddingOut: $("box-padding-out"),
    shadowColour: $("colour-shadow"),
    shadowOpacity: $("shadow-opacity"),
    shadowOpacityOut: $("shadow-opacity-out"),
    shadowDistance: $("shadow-distance"),
    shadowDistanceOut: $("shadow-distance-out"),
    shadowAngle: $("shadow-angle"),
    shadowAngleOut: $("shadow-angle-out"),
  };

  function opaqueHex(value) {
    return String(value || "#000000").slice(0, 7);
  }

  function apply() {
    const draft = getDraft();
    if (!draft) return;
    const colors = draft.colors || {};
    const box = draft.box || {};
    const shadow = draft.shadow || {};

    els.boxColour.value = opaqueHex(colors.box);
    const boxAlpha = alphaToPercent(alphaOf(colors.box));
    els.boxOpacity.value = String(boxAlpha);
    els.boxOpacityOut.textContent = boxAlpha + "%";
    const padding = box.padding == null ? DEFAULTS.padding : Number(box.padding);
    els.boxPadding.value = String(Math.round(padding * 100));
    els.boxPaddingOut.textContent = Math.round(padding * 100) + "%";

    els.shadowColour.value = opaqueHex(colors.shadow);
    const shadowAlpha = alphaToPercent(alphaOf(colors.shadow));
    els.shadowOpacity.value = String(shadowAlpha);
    els.shadowOpacityOut.textContent = shadowAlpha + "%";
    const distance = shadow.distance == null ? DEFAULTS.distance : Number(shadow.distance);
    els.shadowDistance.value = String(distance);
    els.shadowDistanceOut.textContent = distance.toFixed(1) + " px";
    const angle = shadow.angle == null ? DEFAULTS.angle : Number(shadow.angle);
    els.shadowAngle.value = String(angle);
    els.shadowAngleOut.textContent = Math.round(angle) + "°";
  }

  function edit(fn) {
    const draft = getDraft();
    if (!draft) return;
    if (!draft.colors) draft.colors = {};
    if (!draft.box) draft.box = { padding: DEFAULTS.padding };
    if (!draft.shadow) draft.shadow = { angle: DEFAULTS.angle, distance: DEFAULTS.distance };
    fn(draft);
    apply();
    onChange();
  }

  els.boxColour.addEventListener("input", () => edit((d) => {
    d.colors.box = withAlpha(els.boxColour.value, alphaOf(d.colors.box));
  }));
  els.boxOpacity.addEventListener("input", () => edit((d) => {
    d.colors.box = withAlpha(d.colors.box, percentToAlpha(els.boxOpacity.value));
  }));
  els.boxPadding.addEventListener("input", () => edit((d) => {
    d.box.padding = Number(els.boxPadding.value) / 100;
  }));
  els.shadowColour.addEventListener("input", () => edit((d) => {
    d.colors.shadow = withAlpha(els.shadowColour.value, alphaOf(d.colors.shadow));
  }));
  els.shadowOpacity.addEventListener("input", () => edit((d) => {
    d.colors.shadow = withAlpha(d.colors.shadow, percentToAlpha(els.shadowOpacity.value));
  }));
  els.shadowDistance.addEventListener("input", () => edit((d) => {
    d.shadow.distance = Number(els.shadowDistance.value);
  }));
  els.shadowAngle.addEventListener("input", () => edit((d) => {
    d.shadow.angle = Number(els.shadowAngle.value);
  }));

  root.AshStyleEffects = Object.assign(api, {
    init(options) {
      getDraft = (options && options.getDraft) || getDraft;
      onChange = (options && options.onChange) || onChange;
    },
    apply,
  });
})(typeof window !== "undefined" ? window : this);
