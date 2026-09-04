/* Style editor (spec 7A): the picker, the form in four tabs, the live
   sample set in the real font, and Save / Save as / Duplicate / Delete /
   Reset with inline prompts. The preview on real footage is
   style_editor_preview.js. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const styleList = $("style-list");
  const styleFilter = $("style-filter");
  const styleCount = $("style-count");
  const duplicateBtn = $("duplicate-btn");
  const resetBtn = $("reset-btn");
  const deleteBtn = $("delete-btn");
  const editingName = $("editing-name");
  const editingBadge = $("editing-badge");
  const sample = $("sample");
  const fontSelect = $("font-select");
  const sizeInput = $("size-input");
  const spacingInput = $("spacing-input");
  const uppercaseCheck = $("uppercase-check");
  const colourText = $("colour-text");
  const colourActive = $("colour-active");
  const colourOutline = $("colour-outline");
  const colourBox = $("colour-box");
  const activeWordGroup = $("active-word-effect-group");
  const entranceGroup = $("entrance-effect-group");
  const positionGroup = $("position-group");
  const alignGroup = $("align-group");
  const saveBtn = $("save-btn");
  const saveAsBtn = $("save-as-btn");
  const saveStatus = $("save-status");
  const namePrompt = $("name-prompt");
  const nameInput = $("name-input");
  const deletePrompt = $("delete-prompt");

  // Radio option groups, in display order. Values match
  // ash_captions.styles.schema's ACTIVE_WORD_EFFECTS / TRANSITION_EFFECTS /
  // POSITIONS / ALIGNS. "box"/"scale_box" put one word on screen at a time;
  // "card_box" keeps the whole caption on one bar (see schema.py).
  const ACTIVE_WORD_EFFECTS = [
    ["none", "None"], ["pop", "Pop"], ["box", "Box highlight (one word at a time)"],
    ["scale_box", "Scale + box (one word at a time)"],
    ["card_box", "Bar behind the whole caption"], ["karaoke", "Karaoke fill"],
    ["shake", "Shake"], ["glow", "Glow"],
  ];
  const ENTRANCE_EFFECTS = [["none", "None"], ["fade", "Fade"], ["rise", "Rise"], ["slide", "Slide"]];
  const POSITIONS = [["bottom", "Bottom"], ["center", "Center"], ["top", "Top"], ["lower_third", "Lower third"]];
  const ALIGNS = [["left", "Left"], ["center", "Centre"], ["right", "Right"]];
  const DEFAULT_ALIGN = "center"; // schema default for styles saved before `align` existed

  let styles = []; // [{name, shipped, customized_locally, definition}]
  let selectedName = null;
  let draft = null; // the in-progress, unsaved style definition being edited

  // ---- Tabs ----

  const tabs = Array.from(document.querySelectorAll(".tab"));
  function selectTab(tab) {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
    }
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (e) => {
      const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!delta) return;
      e.preventDefault();
      const next = tabs[(i + delta + tabs.length) % tabs.length];
      selectTab(next);
      next.focus();
    });
  });
  selectTab(tabs[0]);

  // ---- Radio groups (built once) ----

  function buildRadioGroup(container, name, options, onChange) {
    container.innerHTML = "";
    for (const [value, label] of options) {
      const wrapper = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = value;
      input.id = `${name}-${value}`;
      input.addEventListener("change", () => { if (input.checked) { onChange(value); renderSample(); } });
      wrapper.appendChild(input);
      wrapper.appendChild(document.createTextNode(label));
      container.appendChild(wrapper);
    }
  }
  function setRadioValue(container, value) {
    const input = container.querySelector(`input[value="${CSS.escape(value)}"]`);
    if (input) input.checked = true;
  }
  buildRadioGroup(activeWordGroup, "active-word-effect", ACTIVE_WORD_EFFECTS, (value) => { draft.active_word.effect = value; });
  buildRadioGroup(entranceGroup, "entrance-effect", ENTRANCE_EFFECTS, (value) => { draft.entrance.effect = value; });
  buildRadioGroup(positionGroup, "position", POSITIONS, (value) => { draft.layout.position = value; });
  buildRadioGroup(alignGroup, "align", ALIGNS, (value) => { draft.layout.align = value; });

  // ---- Loading ----

  async function loadJson(url, what) {
    const res = await AshApi.request(url);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, `Couldn't load ${what}`));
    return res.json();
  }

  async function loadFonts() {
    let fonts;
    let files = [];
    try {
      [fonts, files] = await Promise.all([loadJson("/api/fonts", "the font list"), loadJson("/api/fonts/files", "the font files").catch(() => [])]);
    } catch (err) {
      showSaveStatus(`${err.message}. Refresh the page; if it keeps happening, restart ASH Captions.`, false);
      return;
    }
    fontSelect.innerHTML = "";
    for (const font of fonts) {
      const opt = document.createElement("option");
      opt.value = font;
      opt.textContent = font;
      fontSelect.appendChild(opt);
    }
    // The sample text is set in the real bundled faces.
    const rules = files.map((f) => `@font-face{font-family:${JSON.stringify(f.family)};src:url(${JSON.stringify(f.url)});font-display:swap;}`);
    const style = document.createElement("style");
    style.textContent = rules.join("\n");
    document.head.appendChild(style);
    if (draft) applyDraftToForm();
  }

  async function loadStyles(selectAfter) {
    try {
      styles = await loadJson("/api/styles", "the style list");
    } catch (err) {
      showSaveStatus(`${err.message}. Refresh the page; if it keeps happening, restart ASH Captions.`, false);
      return;
    }
    renderStyleList();
    const toSelect = selectAfter && styles.some((s) => s.name === selectAfter) ? selectAfter : (styles[0] && styles[0].name);
    // Awaited: selectStyle() clears the status line, and saveAs() writes
    // "Saved." right after this returns.
    if (toSelect) await selectStyle(toSelect);
  }

  function pickerTag(style) {
    if (style.customized_locally) return { text: "built-in, edited", cls: "custom" };
    return style.shipped ? { text: "built-in", cls: "" } : { text: "custom", cls: "custom" };
  }

  function renderStyleList() {
    const query = styleFilter.value.trim().toLowerCase();
    styleList.innerHTML = "";
    let shown = 0;
    for (const style of styles) {
      if (query && !style.name.toLowerCase().includes(query)) continue;
      shown++;
      const tag = pickerTag(style);
      const item = document.createElement("div");
      item.className = "style-item" + (style.name === selectedName ? " selected" : "");
      item.tabIndex = 0;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", style.name === selectedName ? "true" : "false");
      const name = document.createElement("span");
      name.textContent = style.name;
      const tagEl = document.createElement("span");
      tagEl.className = `tag${tag.cls ? ` ${tag.cls}` : ""}`;
      tagEl.textContent = tag.text;
      item.append(name, tagEl);
      item.addEventListener("click", () => selectStyle(style.name));
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectStyle(style.name); }
      });
      styleList.appendChild(item);
    }
    styleCount.textContent = query ? `${shown} of ${styles.length}` : `${styles.length} looks`;
  }
  styleFilter.addEventListener("input", renderStyleList);

  async function selectStyle(name) {
    const res = await AshApi.request(`/api/styles/${encodeURIComponent(name)}`);
    if (!res.ok) {
      showSaveStatus(await AshApi.errorDetail(res, `Couldn't load "${name}"`), false);
      return;
    }
    const style = await res.json();
    selectedName = name;
    draft = normalizeDraft(style.definition, name);
    applyDraftToForm();
    renderStyleList();
    editingName.textContent = name;
    hidePrompts();
    if (style.customized_locally) {
      editingBadge.textContent = "Built-in, customized locally";
      showSaveStatus(`Every job using "${name}" (the watch folder too) gets this edited version, not the original.`, true);
    } else {
      editingBadge.textContent = style.shipped ? "Built-in" : "Custom";
      hideSaveStatus();
    }
    deleteBtn.hidden = style.shipped;
    resetBtn.hidden = !style.shipped;
  }

  // A deep copy with the fields this form edits guaranteed present.
  function normalizeDraft(definition, name) {
    const copy = JSON.parse(JSON.stringify(definition));
    copy.name = name;
    copy.layout = copy.layout || {};
    if (!copy.layout.align) copy.layout.align = DEFAULT_ALIGN;
    return copy;
  }

  // `colors.shadow` and `exit` are deliberately not wired to any input;
  // they round-trip unchanged through Save.
  function applyDraftToForm() {
    fontSelect.value = draft.font;
    sizeInput.value = draft.size;
    spacingInput.value = draft.letter_spacing;
    uppercaseCheck.checked = draft.uppercase;
    colourText.value = toHex6(draft.colors.text);
    colourActive.value = toHex6(draft.colors.active);
    colourOutline.value = toHex6(draft.colors.outline);
    colourBox.value = toHex6(draft.colors.box);
    setRadioValue(activeWordGroup, draft.active_word.effect);
    setRadioValue(entranceGroup, draft.entrance.effect);
    setRadioValue(positionGroup, draft.layout.position);
    setRadioValue(alignGroup, draft.layout.align || DEFAULT_ALIGN);
    renderSample();
  }

  // <input type="color"> only accepts #RRGGBB; styles allow an alpha suffix
  // (#RRGGBBAA), preserved in `draft` and just not shown in the swatch.
  function toHex6(hex) { return (hex || "#FFFFFF").slice(0, 7); }
  function withPreservedAlpha(previousHex, newHex6) {
    const alpha = (previousHex || "").length === 9 ? previousHex.slice(7) : "";
    return newHex6 + alpha;
  }

  // ---- The live sample: "Pick this look" in the draft's own type ----

  function renderSample() {
    if (!draft) return;
    const c = draft.colors || {};
    const effect = (draft.active_word || {}).effect;
    sample.className = `sample ${draft.layout.position || "bottom"} ${draft.layout.align || DEFAULT_ALIGN}`;
    sample.style.fontFamily = `${JSON.stringify(draft.font || "Inter")}, sans-serif`;
    sample.style.fontSize = `${Math.round(Math.min(44, Math.max(16, (draft.size || 72) / 2.2)))}px`;
    sample.style.letterSpacing = `${(draft.letter_spacing || 0) * 0.02}em`;
    sample.style.textTransform = draft.uppercase ? "uppercase" : "none";
    sample.style.color = c.text || "#fff";
    sample.style.textShadow = `0 0 2px ${c.outline || "#000"}, 0 0 4px ${c.outline || "#000"}, 0 2px 4px ${c.shadow || "transparent"}`;
    sample.innerHTML = "";
    const boxed = ["box", "scale_box"].includes(effect);
    const barred = effect === "card_box";
    ["Pick", "this", "look"].forEach((word, i) => {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = word;
      if (barred && c.box) w.style.background = c.box;
      if (i === 1) {
        w.style.color = c.active || c.text || "#fff";
        if (boxed && c.box) w.style.background = c.box;
        if (effect === "glow") w.style.textShadow = `0 0 10px ${c.active || "#fff"}`;
        if (effect === "pop" || effect === "scale_box") w.style.transform = "scale(1.1)";
      }
      sample.appendChild(w);
    });
  }

  // ---- Field bindings ----

  fontSelect.addEventListener("change", () => { draft.font = fontSelect.value; renderSample(); });
  sizeInput.addEventListener("input", () => { draft.size = Number(sizeInput.value) || draft.size; renderSample(); });
  spacingInput.addEventListener("input", () => { draft.letter_spacing = Number(spacingInput.value) || 0; renderSample(); });
  uppercaseCheck.addEventListener("change", () => { draft.uppercase = uppercaseCheck.checked; renderSample(); });
  colourText.addEventListener("input", () => { draft.colors.text = withPreservedAlpha(draft.colors.text, colourText.value); renderSample(); });
  colourActive.addEventListener("input", () => { draft.colors.active = withPreservedAlpha(draft.colors.active, colourActive.value); renderSample(); });
  colourOutline.addEventListener("input", () => { draft.colors.outline = withPreservedAlpha(draft.colors.outline, colourOutline.value); renderSample(); });
  colourBox.addEventListener("input", () => { draft.colors.box = withPreservedAlpha(draft.colors.box, colourBox.value); renderSample(); });

  // ---- Save / Save as / Duplicate / Delete / Reset ----

  function showSaveStatus(message, ok) { saveStatus.textContent = message; saveStatus.className = `hint ${ok ? "ok" : "err"}`; }
  function hideSaveStatus() { saveStatus.className = "hint"; saveStatus.textContent = ""; }
  function hidePrompts() { namePrompt.hidden = true; deletePrompt.hidden = true; }

  async function saveAs(name) {
    if (!name || !name.trim()) return;
    const res = await AshApi.request(`/api/styles/${encodeURIComponent(name.trim())}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    if (!res.ok) {
      showSaveStatus(await AshApi.errorDetail(res, "Could not save this style"), false);
      return;
    }
    await loadStyles(name.trim());
    showSaveStatus("Saved.", true);
  }

  function askName(label, suggestion) {
    deletePrompt.hidden = true;
    $("name-prompt-label").textContent = label;
    nameInput.value = suggestion;
    namePrompt.hidden = false;
    nameInput.focus();
    nameInput.select();
  }
  $("name-ok-btn").addEventListener("click", () => { namePrompt.hidden = true; saveAs(nameInput.value); });
  $("name-cancel-btn").addEventListener("click", () => { namePrompt.hidden = true; });
  nameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); namePrompt.hidden = true; saveAs(nameInput.value); }
    if (e.key === "Escape") namePrompt.hidden = true;
  });

  saveBtn.addEventListener("click", () => saveAs(selectedName));
  saveAsBtn.addEventListener("click", () => askName("Save this style as", `${selectedName} copy`));
  duplicateBtn.addEventListener("click", () => { if (selectedName) askName("Duplicate as", `${selectedName} copy`); });

  deleteBtn.addEventListener("click", () => {
    if (!selectedName) return;
    namePrompt.hidden = true;
    $("delete-prompt-text").textContent = `Delete "${selectedName}"? This can't be undone.`;
    deletePrompt.hidden = false;
    $("delete-ok-btn").focus();
  });
  $("delete-cancel-btn").addEventListener("click", () => { deletePrompt.hidden = true; });
  $("delete-ok-btn").addEventListener("click", async () => {
    deletePrompt.hidden = true;
    const res = await AshApi.request(`/api/styles/${encodeURIComponent(selectedName)}`, { method: "DELETE" });
    if (!res.ok) {
      showSaveStatus(await AshApi.errorDetail(res, "Could not delete this style"), false);
      return;
    }
    await loadStyles();
    showSaveStatus("Deleted.", true);
  });

  resetBtn.addEventListener("click", async () => {
    if (!selectedName) return;
    // Removes the local override on the server, so the shipped definition
    // is back in force for every job -- not just loaded into the form.
    const res = await AshApi.request(`/api/styles/${encodeURIComponent(selectedName)}/reset`, { method: "POST" });
    if (!res.ok) {
      showSaveStatus(await AshApi.errorDetail(res, "Couldn't reset to the built-in version"), false);
      return;
    }
    const style = await res.json();
    draft = normalizeDraft(style.definition, selectedName);
    applyDraftToForm();
    await loadStyles(selectedName);
    showSaveStatus("Reset: the built-in version is back in use.", true);
  });

  // ---- Boot ----

  loadFonts();
  loadStyles();
  AshEditorPreview.init({ getDraft: () => draft });
})();
