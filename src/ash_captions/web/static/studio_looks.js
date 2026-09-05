/* The Studio's looks strip: cards grouped by position, a filter box,
   keyboard movement (left/right within a group, up/down between groups,
   Enter applies), and Compare, which flips between the two looks picked
   most recently. studio.js owns the job and the player; it hands this
   module the styles and a callback to apply one. */
(function () {
  "use strict";

  const POSITION_ORDER = ["top", "center", "bottom", "lower_third"];
  const POSITION_LABEL = { top: "Top", center: "Centre", bottom: "Bottom", lower_third: "Lower third" };

  function glyph(layout) {
    const span = document.createElement("span");
    const position = POSITION_ORDER.includes(layout.position) ? layout.position : "bottom";
    const align = ["left", "center", "right"].includes(layout.align) ? layout.align : null;
    span.className = `look-glyph ${position}${align ? ` align-${align}` : ""}`;
    span.title = POSITION_LABEL[position] + (align ? `, ${align}` : "");
    span.appendChild(document.createElement("i"));
    return span;
  }

  // Static fallback, shown until look_card.js finishes loading (see
  // ensureLookCard below) or if JASSUB can't start at all.
  function staticSampleFor(d) {
    const colors = d.colors || {};
    const active = d.active_word || {};
    const sample = document.createElement("div");
    sample.className = "look-sample";
    sample.style.fontFamily = `${JSON.stringify(d.font || "Inter")}, sans-serif`;
    sample.style.fontSize = `${Math.round(Math.min(24, Math.max(15, (d.size || 72) / 3.8)))}px`;
    sample.style.color = colors.text || "#fff";
    sample.style.letterSpacing = `${(d.letter_spacing || 0) * 0.02}em`;
    sample.style.textTransform = d.uppercase ? "uppercase" : "none";
    sample.style.textShadow = `0 0 2px ${colors.outline || "#000"}, 0 2px 3px ${colors.shadow || "transparent"}`;
    ["Pick", "this", "look"].forEach((word, i) => {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = word;
      if (i === 1) {
        w.style.color = colors.active || colors.text || "#fff";
        const boxed = active.box || ["box", "scale_box", "card_box"].includes(active.effect);
        if (boxed && colors.box) w.style.background = colors.box;
        if (active.effect === "glow") w.style.textShadow = `0 0 8px ${colors.active || "#fff"}`;
      }
      sample.appendChild(w);
    });
    return sample;
  }

  // look_card.js (spec 4) is shared with the Styles page but studio.html
  // is off limits to this track, so it's loaded here at runtime instead
  // of with a <script> tag. jassub.umd.js is already on the Studio page.
  let lookCardPromise = null;
  function ensureLookCard() {
    if (window.AshLookCard) return Promise.resolve(true);
    if (lookCardPromise) return lookCardPromise;
    lookCardPromise = new Promise((resolve) => {
      if (!document.querySelector('link[data-ash-look-card]')) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = "/static/look_card.css";
        link.dataset.ashLookCard = "1";
        document.head.appendChild(link);
      }
      const assScript = document.createElement("script");
      assScript.src = "/static/look_card_ass.js";
      assScript.onload = () => {
        const script = document.createElement("script");
        script.src = "/static/look_card.js";
        script.onload = () => resolve(!!window.AshLookCard);
        script.onerror = () => resolve(false);
        document.head.appendChild(script);
      };
      assScript.onerror = () => resolve(false);
      document.head.appendChild(assScript);
    });
    return lookCardPromise;
  }

  function sampleFor(style, lookCardReady) {
    const d = style.definition || {};
    if (!lookCardReady || !window.AshLookCard) return staticSampleFor(d);
    // A plain .look-sample wrapper, sized by studio.css exactly as the
    // static version was, with the animated card filling it -- kept as
    // two elements rather than one so look_card.css's generic sizing
    // never has to fight studio.css's fixed height for the same element.
    const wrap = document.createElement("div");
    wrap.className = "look-sample";
    wrap.appendChild(window.AshLookCard.create(d, { fontDivisor: 3.8, fontMin: 15, fontMax: 24 }));
    return wrap;
  }

  // refs: { list, filter, hint, compareBtn }; onApply(name) -> Promise
  function createLooks(refs, onApply) {
    const { list, filter, hint, compareBtn } = refs;
    let styles = [];
    let enabled = true;
    let current = null; // the job's preset
    let focused = null; // keyboard cursor (a style name)
    let previous = null; // for Compare: the look before `current`
    let rows = []; // [[name, ...]] per group, in display order, after filtering
    let lookCardReady = false;
    let sampleCards = []; // AshLookCard elements from the current render(), for dispose before the next one

    ensureLookCard().then((ok) => {
      lookCardReady = ok;
      if (ok) render(); // upgrade whatever's on screen from the static fallback
    });

    function card(style) {
      const d = style.definition || {};
      const el = document.createElement("button");
      el.type = "button";
      el.className = "look";
      el.dataset.name = style.name;
      el.setAttribute("role", "option");
      el.tabIndex = -1;
      el.disabled = !enabled;
      el.title = enabled ? `Preview "${style.name}"` : "Looks can't be previewed on the burned output";
      const foot = document.createElement("div");
      foot.className = "look-foot";
      const name = document.createElement("span");
      name.className = "look-name";
      name.textContent = style.name;
      if (style.customized_locally || !style.shipped) {
        const tag = document.createElement("span");
        tag.className = "look-tag";
        tag.textContent = style.customized_locally ? "edited" : "custom";
        name.appendChild(document.createTextNode(" "));
        name.appendChild(tag);
      }
      const sample = sampleFor(style, lookCardReady);
      const lookCard = sample.querySelector(".ash-look-card");
      if (lookCard) sampleCards.push(lookCard);
      foot.append(name, glyph(d.layout || {}));
      el.append(sample, foot);
      el.addEventListener("click", () => { focused = style.name; apply(style.name); });
      return el;
    }

    function matches(style, query) {
      if (!query) return true;
      const d = style.definition || {};
      const hay = [style.name, d.font, (d.layout || {}).position, (d.layout || {}).align, (d.active_word || {}).effect].filter(Boolean).join(" ").toLowerCase();
      return query.split(/\s+/).every((word) => hay.includes(word));
    }

    function render() {
      const query = filter.value.trim().toLowerCase();
      if (window.AshLookCard) for (const el of sampleCards) window.AshLookCard.dispose(el);
      sampleCards = [];
      list.innerHTML = "";
      rows = [];
      const groups = new Map(POSITION_ORDER.map((p) => [p, []]));
      for (const style of styles) {
        if (!matches(style, query)) continue;
        const position = ((style.definition || {}).layout || {}).position;
        (groups.get(position) || groups.get("bottom")).push(style);
      }
      for (const [position, group] of groups) {
        if (group.length === 0) continue;
        const section = document.createElement("section");
        section.className = "look-group";
        const h3 = document.createElement("h3");
        h3.textContent = `${POSITION_LABEL[position]} · ${group.length}`;
        section.appendChild(h3);
        for (const style of group) section.appendChild(card(style));
        list.appendChild(section);
        rows.push(group.map((s) => s.name));
      }
      if (rows.length === 0) {
        const empty = document.createElement("div");
        empty.className = "looks-empty";
        empty.textContent = query ? `No look matches "${filter.value.trim()}".` : "No looks installed.";
        list.appendChild(empty);
      }
      highlight(true);
    }

    function highlight(scroll) {
      for (const el of list.querySelectorAll(".look")) {
        el.classList.toggle("current", el.dataset.name === current);
        el.classList.toggle("focused", el.dataset.name === focused && focused !== current);
        el.setAttribute("aria-selected", el.dataset.name === current ? "true" : "false");
      }
      const target = list.querySelector(`.look[data-name="${CSS.escape(focused || current || "")}"]`);
      if (target) {
        list.setAttribute("aria-activedescendant", target.id || (target.id = `look-${rows.flat().indexOf(target.dataset.name)}`));
        if (scroll) target.scrollIntoView({ block: "nearest" });
      }
      compareBtn.disabled = !(enabled && previous && previous !== current);
      compareBtn.title = compareBtn.disabled ? "Pick two looks, then flip between them (C)" : `Flip to ${previous} (C)`;
    }

    async function apply(name) {
      if (!enabled || name === current) return;
      const before = current;
      const ok = await onApply(name);
      if (ok) { previous = before; current = name; focused = name; }
      highlight(true);
    }

    function locate(name) {
      for (let r = 0; r < rows.length; r++) {
        const c = rows[r].indexOf(name);
        if (c >= 0) return [r, c];
      }
      return null;
    }

    function move(dr, dc) {
      if (rows.length === 0) return;
      const pos = locate(focused) || locate(current) || [0, -1];
      let [r, c] = pos;
      if (dc) c = Math.max(0, Math.min(rows[r].length - 1, c + dc));
      if (dr) { r = Math.max(0, Math.min(rows.length - 1, r + dr)); c = Math.min(c < 0 ? 0 : c, rows[r].length - 1); }
      if (c < 0) c = 0;
      focused = rows[r][c];
      highlight(true);
    }

    function onKey(e) {
      const keys = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] };
      if (keys[e.key]) { e.preventDefault(); move(...keys[e.key]); return true; }
      if (e.key === "Enter" && focused) { e.preventDefault(); apply(focused); return true; }
      if ((e.key === "c" || e.key === "C") && !e.ctrlKey && !e.metaKey && !e.altKey) { compare(); return true; }
      if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        const flat = rows.flat();
        focused = e.key === "Home" ? flat[0] : flat[flat.length - 1];
        highlight(true);
        return true;
      }
      return false;
    }

    function compare() {
      if (compareBtn.disabled || !previous) return;
      apply(previous); // apply() records `current` as the new previous, so C flips back and forth
    }

    filter.addEventListener("input", render);
    filter.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); list.focus(); if (!focused) move(0, 0); }
      if (e.key === "Escape") { filter.value = ""; render(); }
    });
    list.addEventListener("keydown", onKey);
    compareBtn.addEventListener("click", compare);

    return {
      setStyles(next, isEnabled, currentName) {
        styles = next;
        enabled = isEnabled;
        current = currentName;
        focused = currentName;
        if (!isEnabled) hint.textContent = "The original footage is gone, so this is the burned result and looks can't be changed.";
        render();
      },
      setCurrent(name) { current = name; focused = name; highlight(true); },
      handleKey: onKey,
      compare,
      focus() { list.focus(); },
    };
  }

  window.AshStudioLooks = { createLooks, POSITION_LABEL };
})();
