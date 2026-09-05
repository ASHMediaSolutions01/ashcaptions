/* The sample .ass builder behind look_card.js (v0.6 spec 4): a 3-word
   sample card, "Pick this look", exercising a style's entrance, active-
   word effect and exit over one ~2s loop.

   This is a deliberate, hand-kept port of the tag formulas in
   styles/render.py, styles/ass_format.py and styles/render_glow.py.
   Python can't be imported into a browser, and a server round trip per
   card (or per keystroke, for 36 cards re-filtered on every keypress) is
   the "too heavy" case the spec calls out -- so the browser builds its
   own small .ass and hands it straight to JASSUB. If the Python formulas
   change, this file drifts and should be fixed to match them; the Python
   is always the source of truth for what a real job renders.

   No DOM here on purpose: everything in this file runs (and is exercised
   by tests/test_web/js/look_card_ass.test.mjs) under plain Node, with no
   browser at all. look_card.js is the DOM/JASSUB half. */
(function (root) {
  "use strict";

  const LOOP_MS = 2000;
  const PLAY_RES = [1080, 1920]; // styles/render.py's DEFAULT_PLAY_RES
  const SAMPLE_WORDS = ["Pick", "this", "look"];

  const RISE_OFFSET_PX = 46;
  const SLIDE_OFFSET_PX = 160;
  const POP_HALF_MS = 90;
  const SHAKE_QUARTER_MS = 45;
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
  function formatAssTime(seconds) {
    seconds = Math.max(seconds, 0);
    const totalCs = Math.round(seconds * 100);
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
  function safeStyleName(name) {
    return String(name || "").replace(/,/g, "").replace(/ /g, "_") || "STYLE";
  }
  function escapeAssText(text) {
    return String(text).replace(/[{}\\]/g, (ch) => ESCAPE_MAP[ch]);
  }
  function prepareWordText(text, style) {
    const upper = style.uppercase ? String(text).toUpperCase() : text;
    return escapeAssText(upper);
  }

  function styleField(o) {
    return (
      `Style: ${o.name},${o.font},${o.size},` +
      `${assStyleColour(o.primary)},${assStyleColour(o.secondary)},` +
      `${assStyleColour(o.outlineColour)},${assStyleColour(o.backColour)},` +
      "0,0,0,0,100,100,0,0," +
      `${o.borderStyle},${o.outlineWidthPx},${o.shadow},${o.alignment},` +
      `${o.layout.margin_l},${o.layout.margin_r},${o.layout.margin_v},1`
    );
  }

  function assHeader(style, baseName, boxName, width, height) {
    const layout = style.layout || {};
    const alignment = assAlignment(layout.position || "bottom", layout.align || "center");
    const outline = outlineWidth(style);
    const shadowWidth = String(style.colors.shadow).toUpperCase() !== "#00000000" ? 2 : 0;
    const boxPadding = Math.max(8, Math.round(style.size * 0.28));
    const cardBox = style.active_word.effect === "card_box";
    const baseStyle = styleField({
      name: baseName, font: style.font, size: style.size,
      primary: style.colors.active, secondary: style.colors.text,
      outlineColour: cardBox ? style.colors.box : style.colors.outline,
      backColour: cardBox ? style.colors.box : style.colors.shadow,
      borderStyle: cardBox ? 3 : 1,
      outlineWidthPx: cardBox ? boxPadding : outline,
      shadow: cardBox ? 0 : shadowWidth,
      alignment, layout,
    });
    const boxStyle = styleField({
      name: boxName, font: style.font, size: style.size,
      primary: style.colors.active, secondary: style.colors.active,
      outlineColour: style.colors.box, backColour: style.colors.box,
      borderStyle: 3, outlineWidthPx: boxPadding, shadow: 0,
      alignment, layout,
    });
    return (
      "[Script Info]\n" +
      "ScriptType: v4.00+\n" +
      `PlayResX: ${width}\n` +
      `PlayResY: ${height}\n` +
      "ScaledBorderAndShadow: yes\n\n" +
      "[V4+ Styles]\n" +
      "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, " +
      "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, " +
      "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, " +
      "Alignment, MarginL, MarginR, MarginV, Encoding\n" +
      `${baseStyle}\n${boxStyle}\n\n` +
      "[Events]\n" +
      "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    );
  }

  function anchorXY(style, width, height) {
    const layout = style.layout || {};
    const an = assAlignment(layout.position || "bottom", layout.align || "center");
    const row = Math.floor((an - 1) / 3);
    const column = (an - 1) % 3;
    const y = row === 0 ? height - layout.margin_v : row === 2 ? layout.margin_v : height / 2;
    const x = column === 0 ? layout.margin_l : column === 2 ? width - layout.margin_r : width / 2;
    return [x, y];
  }

  function entranceTag(style, x, y, eventMs) {
    const effect = style.entrance.effect;
    const duration = Math.min(style.entrance.duration_ms, eventMs);
    if (effect === "fade" && duration) return `\\fad(${duration},0)`;
    if ((effect === "rise" || effect === "slide") && duration) {
      const [dx, dy] = effect === "rise" ? [0, RISE_OFFSET_PX] : [SLIDE_OFFSET_PX, 0];
      return `\\move(${num(x + dx)},${num(y + dy)},${num(x)},${num(y)},0,${duration})`;
    }
    return "";
  }
  function exitTag(style, x, y, eventMs) {
    const effect = style.exit.effect;
    const duration = Math.min(style.exit.duration_ms, eventMs);
    if (effect === "fade" && duration) return `\\fad(0,${duration})`;
    if ((effect === "rise" || effect === "slide") && duration) {
      const [dx, dy] = effect === "rise" ? [0, -RISE_OFFSET_PX] : [-SLIDE_OFFSET_PX, 0];
      const t1 = Math.max(0, eventMs - duration);
      return `\\move(${num(x)},${num(y)},${num(x + dx)},${num(y + dy)},${t1},${eventMs})`;
    }
    return "";
  }
  function tagKind(tag) {
    return tag.startsWith("\\fad(") ? "fad" : "move";
  }

  function leadingOverride(style, x, y, isFirst, isLast, eventMs) {
    const tags = [];
    if (style.letter_spacing) tags.push(`\\fsp${num(style.letter_spacing)}`);
    const eTag = isFirst ? entranceTag(style, x, y, eventMs) : "";
    const xTag = isLast ? exitTag(style, x, y, eventMs) : "";
    if (eTag && xTag && tagKind(eTag) === tagKind(xTag)) {
      if (tagKind(eTag) === "fad") {
        let entranceMs = style.entrance.effect === "fade" ? Math.min(style.entrance.duration_ms, eventMs) : 0;
        let exitMs = style.exit.effect === "fade" ? Math.min(style.exit.duration_ms, eventMs) : 0;
        if (entranceMs + exitMs > eventMs) {
          entranceMs = Math.floor(eventMs / 2);
          exitMs = eventMs - entranceMs;
        }
        tags.push(`\\fad(${entranceMs},${exitMs})`);
      } else {
        tags.push(eTag);
      }
    } else {
      if (eTag) tags.push(eTag);
      if (xTag) tags.push(xTag);
    }
    return tags.join("");
  }

  function scaleTransformTags(scale, halfMs) {
    const half = halfMs || POP_HALF_MS;
    const pct = Math.round(scale * 100);
    return `\\t(0,${half},\\fscx${pct}\\fscy${pct})\\t(${half},${2 * half},\\fscx100\\fscy100)`;
  }
  function popScaleTags(style, eventMs) {
    const scale = Math.round(style.active_word.scale * 100);
    const d = Math.min(POP_HALF_MS, Math.max(1, Math.floor(eventMs / 2)));
    return `{\\t(0,${d},\\fscx${scale}\\fscy${scale})\\t(${d},${2 * d},\\fscx100\\fscy100)}`;
  }
  function activeWordTags(style, activeColour, textColour) {
    const effect = style.active_word.effect;
    if (effect === "pop" || effect === "glow") {
      return [`\\c${activeColour}${scaleTransformTags(style.active_word.scale)}`, `\\c${textColour}\\fscx100\\fscy100`];
    }
    if (effect === "shake") {
      const q = SHAKE_QUARTER_MS;
      return [
        `\\c${activeColour}\\t(0,${q},\\frz-4)\\t(${q},${2 * q},\\frz4)\\t(${2 * q},${3 * q},\\frz-2)\\t(${3 * q},${4 * q},\\frz0)`,
        `\\c${textColour}\\frz0`,
      ];
    }
    return [`\\c${activeColour}`, `\\c${textColour}`];
  }
  function haloLineText(preparedWords, activeIndex, style) {
    const activeColour = assInlineColour(style.colors.active);
    const openTags =
      `\\1a&HFF&\\3a&H00&\\4a&HFF&\\3c${activeColour}\\bord${glowWidth(style)}\\blur4\\be1` +
      scaleTransformTags(style.active_word.scale);
    const closeTags = `\\alpha&HFF&\\bord${outlineWidth(style)}\\blur0\\be0\\fscx100\\fscy100`;
    return preparedWords
      .map((w, i) => (i === activeIndex ? `{${openTags}}${w}{${closeTags}}` : `{\\alpha&HFF&}${w}`))
      .join(" ");
  }
  function lineText(words, activeIndex, style) {
    const textColour = assInlineColour(style.colors.text);
    const activeColour = assInlineColour(style.colors.active);
    return words
      .map((word, i) => {
        const text = prepareWordText(word, style);
        if (i === activeIndex) {
          const [openTags, closeTags] = activeWordTags(style, activeColour, textColour);
          return `{${openTags}}${text}{${closeTags}}`;
        }
        return `{\\c${textColour}}${text}`;
      })
      .join(" ");
  }
  function dialogueLine(start, end, styleName, text, layer) {
    return `Dialogue: ${layer || 0},${formatAssTime(start)},${formatAssTime(end)},${styleName},,0,0,0,,${text}`;
  }

  // A 3-word sample card, evenly sliced across one LOOP_MS cycle -- close
  // enough to how styles/render.py sizes a per-word event (from the next
  // word's start, or the card's end for the last word) to show the same
  // shape of motion without a real transcript to draw timing from.
  function buildSampleAss(style) {
    const [width, height] = PLAY_RES;
    const baseName = safeStyleName(style.name);
    const boxName = baseName + "_BOX";
    const header = assHeader(style, baseName, boxName, width, height);
    const [x, y] = anchorXY(style, width, height);
    const effect = style.active_word.effect;
    const count = SAMPLE_WORDS.length;
    const sliceMs = Math.floor(LOOP_MS / count);
    const bounds = SAMPLE_WORDS.map((_, i) => [i * sliceMs, i === count - 1 ? LOOP_MS : (i + 1) * sliceMs]);
    const lines = [];

    if (effect === "karaoke") {
      const parts = SAMPLE_WORDS.map((word, i) => {
        const [start, end] = bounds[i];
        return `{\\kf${Math.max(1, Math.round((end - start) / 10))}}${prepareWordText(word, style)}`;
      });
      const leading = leadingOverride(style, x, y, true, true, LOOP_MS);
      const body = parts.join(" ");
      lines.push(dialogueLine(0, LOOP_MS / 1000, baseName, leading ? `{${leading}}${body}` : body));
      return header + lines.join("\n") + "\n";
    }

    const boxed = effect === "box" || effect === "scale_box";
    const glow = effect === "glow";
    const styleName = boxed ? boxName : baseName;
    for (let i = 0; i < count; i++) {
      const [startMs, endMs] = bounds[i];
      const start = startMs / 1000;
      const end = endMs / 1000;
      const eventMs = endMs - startMs;
      const isFirst = i === 0;
      const isLast = i === count - 1;
      const leading = leadingOverride(style, x, y, isFirst, isLast, eventMs);
      const prefix = leading ? `{${leading}}` : "";
      if (boxed) {
        const text = prepareWordText(SAMPLE_WORDS[i], style);
        const scaleTags = effect === "scale_box" ? popScaleTags(style, eventMs) : "";
        lines.push(dialogueLine(start, end, styleName, `${prefix}${scaleTags}${text}`));
      } else if (glow) {
        const prepared = SAMPLE_WORDS.map((w) => prepareWordText(w, style));
        const halo = haloLineText(prepared, i, style);
        const text = lineText(SAMPLE_WORDS, i, style);
        lines.push(dialogueLine(start, end, styleName, prefix + halo, 0));
        lines.push(dialogueLine(start, end, styleName, prefix + text, 1));
      } else {
        const text = lineText(SAMPLE_WORDS, i, style);
        lines.push(dialogueLine(start, end, styleName, prefix + text));
      }
    }
    return header + lines.join("\n") + "\n";
  }

  const api = {
    LOOP_MS,
    SAMPLE_WORDS,
    buildSampleAss,
    assStyleColour,
    assInlineColour,
    formatAssTime,
    assAlignment,
    entranceTag,
    exitTag,
    leadingOverride,
    activeWordTags,
    safeStyleName,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AshLookCardAss = api;
})(typeof window !== "undefined" ? window : this);
