/* The ports of styles/ass_format.py and the text half of
   styles/render_word.py, kept apart from the sample builder that uses
   them (look_card_ass.js).

   Split out when look_card_ass.js crossed the 500-line ceiling the tests
   enforce. The seam is a real one rather than a place to cut: everything
   here answers "what does the Python turn this style into?" -- colours,
   timestamps, alignment, outline and box geometry, where the shadow
   falls, and how a look treats the words it was given. Nothing here
   knows there is such a thing as a card.

   Python is the source of truth for every formula in this file, and
   tests/test_web/test_look_card_drift.py runs the same inputs through
   both and demands the same answer. No DOM: it runs under plain Node. */
(function (root) {
  "use strict";

  const ESCAPE_MAP = { "{": "｛", "}": "｝", "\\": "＼" };

  function num(value) {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }

  function parseHex(colour) {
    const body = String(colour).replace("#", "");
    const r = parseInt(body.slice(0, 2), 16);
    const g = parseInt(body.slice(2, 4), 16);
    const b = parseInt(body.slice(4, 6), 16);
    const a = body.length === 8 ? parseInt(body.slice(6, 8), 16) : 255;
    return [r, g, b, a];
  }
  function hex2(n) {
    return n.toString(16).toUpperCase().padStart(2, "0");
  }
  function assStyleColour(colour) {
    const [r, g, b, a] = parseHex(colour);
    return `&H${hex2(255 - a)}${hex2(b)}${hex2(g)}${hex2(r)}`;
  }
  function assInlineColour(colour) {
    const [r, g, b] = parseHex(colour);
    return `&H${hex2(b)}${hex2(g)}${hex2(r)}&`;
  }
  // Python's round() breaks an exact .5 tie to the *even* number;
  // Math.round breaks it upwards. At 12.345s that is a whole centisecond
  // of disagreement between what a look card shows and what the burn
  // writes -- found by tests/test_web/test_look_card_drift.py, which is
  // the only thing that compares the two rather than comparing this file
  // to numbers a person typed. ass_format.py is the source of truth.
  function roundHalfToEven(value) {
    const below = Math.floor(value);
    const fraction = value - below;
    if (fraction > 0.5) return below + 1;
    if (fraction < 0.5) return below;
    return below % 2 === 0 ? below : below + 1;
  }

  function formatAssTime(seconds) {
    seconds = Math.max(seconds, 0);
    const totalCs = roundHalfToEven(seconds * 100);
    const hours = Math.floor(totalCs / 360000);
    const remH = totalCs % 360000;
    const minutes = Math.floor(remH / 6000);
    const remM = remH % 6000;
    const secs = Math.floor(remM / 100);
    const cs = remM % 100;
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
  }

  const ROW_BASE = { bottom: 1, lower_third: 1, center: 4, top: 7 };
  const COLUMN_OFFSET = { left: 0, center: 1, right: 2 };
  function assAlignment(position, align) {
    return (ROW_BASE[position] || 1) + (COLUMN_OFFSET[align] !== undefined ? COLUMN_OFFSET[align] : 1);
  }
  function outlineWidth(style) {
    return Math.max(1, Math.round(style.size * 0.055));
  }
  function glowWidth(style) {
    const base = outlineWidth(style);
    return Math.max(base + 3, base * 2);
  }
  // Ports of ass_format.box_padding / shadow_visible / shadow_offset /
  // shadow_tags. The shadow left the Style column so it could carry an
  // angle; the two forms render identically (measured), which is why no
  // existing look moved.
  function boxPaddingPx(style) {
    const box = style.box || {};
    const padding = box.padding == null ? 0.28 : Number(box.padding);
    return Math.max(8, Math.round(style.size * padding));
  }
  function shadowGeometry(style) {
    const s = style.shadow || {};
    return { angle: s.angle == null ? 45 : Number(s.angle),
             distance: s.distance == null ? 2.83 : Number(s.distance) };
  }
  function shadowVisible(style) {
    return String(style.colors.shadow).toUpperCase() !== "#00000000"
      && shadowGeometry(style).distance > 0;
  }
  function shadowOffset(style) {
    const g = shadowGeometry(style);
    const rad = (g.angle * Math.PI) / 180;
    // Rounded here, as the Python is: cos(90 degrees) is 6.1e-17, which
    // formats as "0.00" on one axis and "0" on the other.
    const dx = Math.round(g.distance * Math.cos(rad) * 100) / 100;
    const dy = Math.round(g.distance * Math.sin(rad) * 100) / 100;
    return [dx === 0 ? 0 : dx, dy === 0 ? 0 : dy];
  }
  function shadowTags(style) {
    if (!shadowVisible(style)) return "";
    const [dx, dy] = shadowOffset(style);
    return `\\xshad${num(dx)}\\yshad${num(dy)}`;
  }
  function safeStyleName(name) {
    return String(name || "").replace(/,/g, "").replace(/ /g, "_") || "STYLE";
  }
  function escapeAssText(text) {
    return String(text).replace(/[{}\\]/g, (ch) => ESCAPE_MAP[ch]);
  }
  // Hand-kept ports of render_word.apply_case / apply_punctuation. The
  // character sets are the same list in the same order as the Python.
  const STOP_MARKS = new Set(Array.from(".,;:…。、，；：،؛۔"));
  const INTRA_WORD_MARKS = new Set(Array.from("'’-‐‑"));

  function applyCase(text, mode) {
    if (mode === "upper") return String(text).toUpperCase();
    if (mode === "lower") return String(text).toLowerCase();
    return String(text);
  }

  function applyPunctuation(text, mode) {
    const s = String(text);
    if (mode === "no_stops") {
      return Array.from(s).filter((ch) => !STOP_MARKS.has(ch)).join("");
    }
    if (mode !== "none" || !s) return s;
    const chars = Array.from(s);
    return chars
      .filter((ch, i) => {
        if (!/\p{P}/u.test(ch)) return true;
        return INTRA_WORD_MARKS.has(ch) && i > 0 && i < chars.length - 1
          && /\p{L}/u.test(chars[i - 1]) && /\p{L}/u.test(chars[i + 1]);
      })
      .join("");
  }

  function prepareWordText(text, style) {
    const cased = applyCase(applyPunctuation(text, style.punctuation || "keep"),
                            style.case_mode || "as_written");
    return escapeAssText(cased);
  }

  const api = {
    num,
    parseHex,
    hex2,
    assStyleColour,
    assInlineColour,
    roundHalfToEven,
    formatAssTime,
    assAlignment,
    outlineWidth,
    glowWidth,
    boxPaddingPx,
    shadowGeometry,
    shadowVisible,
    shadowOffset,
    shadowTags,
    safeStyleName,
    escapeAssText,
    applyCase,
    applyPunctuation,
    prepareWordText,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AshLookCardStyle = api;
})(typeof window !== "undefined" ? window : this);
