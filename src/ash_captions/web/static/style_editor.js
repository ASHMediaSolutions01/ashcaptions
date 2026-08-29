(function () {
  "use strict";

  const styleList = document.getElementById("style-list");
  const duplicateBtn = document.getElementById("duplicate-btn");
  const resetBtn = document.getElementById("reset-btn");
  const deleteBtn = document.getElementById("delete-btn");

  const editingName = document.getElementById("editing-name");
  const editingBadge = document.getElementById("editing-badge");

  const fontSelect = document.getElementById("font-select");
  const sizeInput = document.getElementById("size-input");
  const spacingInput = document.getElementById("spacing-input");
  const uppercaseCheck = document.getElementById("uppercase-check");
  const colourText = document.getElementById("colour-text");
  const colourActive = document.getElementById("colour-active");
  const colourOutline = document.getElementById("colour-outline");
  const colourBox = document.getElementById("colour-box");
  const activeWordGroup = document.getElementById("active-word-effect-group");
  const entranceGroup = document.getElementById("entrance-effect-group");
  const positionGroup = document.getElementById("position-group");
  const saveBtn = document.getElementById("save-btn");
  const saveAsBtn = document.getElementById("save-as-btn");
  const saveStatus = document.getElementById("save-status");

  const previewPathInput = document.getElementById("preview-path-input");
  const previewTimeInput = document.getElementById("preview-time-input");
  const renderBtn = document.getElementById("render-btn");
  const previewStatus = document.getElementById("preview-status");
  const previewOutput = document.getElementById("preview-output");
  const previewVideo = document.getElementById("preview-video");

  // Radio option groups, in display order. Values match
  // ash_captions.styles.schema's ACTIVE_WORD_EFFECTS / TRANSITION_EFFECTS / POSITIONS.
  const ACTIVE_WORD_EFFECTS = [
    ["none", "None"], ["pop", "Pop"], ["box", "Box highlight"],
    ["scale_box", "Scale + box"], ["karaoke", "Karaoke fill"],
    ["shake", "Shake"], ["glow", "Glow"],
  ];
  const ENTRANCE_EFFECTS = [
    ["none", "None"], ["fade", "Fade"], ["rise", "Rise"], ["slide", "Slide"],
  ];
  const POSITIONS = [
    ["bottom", "Bottom"], ["center", "Center"], ["top", "Top"], ["lower_third", "Lower third"],
  ];

  let styles = []; // [{name, shipped, definition}]
  let selectedName = null;
  let draft = null; // the in-progress, unsaved style definition being edited
  let previewPollTimer = null;

  // ---- Radio groups (built once) ----

  function buildRadioGroup(container, name, options, onChange) {
    container.innerHTML = "";
    for (const [value, label] of options) {
      const id = `${name}-${value}`;
      const wrapper = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = value;
      input.id = id;
      input.addEventListener("change", () => { if (input.checked) onChange(value); });
      wrapper.appendChild(input);
      wrapper.appendChild(document.createTextNode(label));
      container.appendChild(wrapper);
    }
  }

  function setRadioValue(container, value) {
    const input = container.querySelector(`input[value="${CSS.escape(value)}"]`);
    if (input) input.checked = true;
  }

  buildRadioGroup(activeWordGroup, "active-word-effect", ACTIVE_WORD_EFFECTS, (value) => {
    draft.active_word.effect = value;
  });
  buildRadioGroup(entranceGroup, "entrance-effect", ENTRANCE_EFFECTS, (value) => {
    draft.entrance.effect = value;
  });
  buildRadioGroup(positionGroup, "position", POSITIONS, (value) => {
    draft.layout.position = value;
  });

  // ---- Loading ----

  async function loadFonts() {
    const res = await fetch("/api/fonts");
    const fonts = await res.json();
    fontSelect.innerHTML = "";
    for (const font of fonts) {
      const opt = document.createElement("option");
      opt.value = font;
      opt.textContent = font;
      fontSelect.appendChild(opt);
    }
  }

  async function loadStyles(selectAfter) {
    const res = await fetch("/api/styles");
    styles = await res.json();
    renderStyleList();
    const toSelect = selectAfter && styles.some((s) => s.name === selectAfter)
      ? selectAfter
      : (styles[0] && styles[0].name);
    if (toSelect) selectStyle(toSelect);
  }

  function renderStyleList() {
    styleList.innerHTML = "";
    for (const style of styles) {
      const item = document.createElement("div");
      item.className = "style-item" + (style.name === selectedName ? " selected" : "");
      item.innerHTML = `
        <span>${escapeHtml(style.name)}</span>
        <span class="tag${style.shipped ? "" : " custom"}">${style.shipped ? "Built-in" : "Custom"}</span>
      `;
      item.addEventListener("click", () => selectStyle(style.name));
      styleList.appendChild(item);
    }
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  async function selectStyle(name) {
    const res = await fetch(`/api/styles/${encodeURIComponent(name)}`);
    if (!res.ok) return;
    const style = await res.json();
    selectedName = name;
    draft = JSON.parse(JSON.stringify(style.definition));
    draft.name = name;
    applyDraftToForm();
    renderStyleList();
    editingName.textContent = name;
    editingBadge.textContent = style.shipped ? "Built-in" : "Custom";
    deleteBtn.hidden = style.shipped;
    resetBtn.hidden = !style.shipped;
    hideSaveStatus();
  }

  // `draft` is the full style dict (schema fields: name, font, size,
  // uppercase, letter_spacing, colors, active_word, entrance, exit,
  // layout). `applyDraftToForm()`/the field bindings below only touch
  // colors.text/active/outline/box and active_word/entrance/layout.position
  // -- `colors.shadow` and `exit` are deliberately not wired to any input
  // (see the HTML comment above the colour row for why); they stay
  // whatever the selected style had and round-trip unchanged through
  // saveAs()'s JSON.stringify(draft).
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
  }

  // <input type="color"> only accepts #RRGGBB; styles allow an alpha suffix
  // (#RRGGBBAA). The alpha byte is preserved in `draft` and just not shown
  // in the swatch -- editing the swatch keeps whatever alpha was loaded.
  function toHex6(hex) {
    return (hex || "#FFFFFF").slice(0, 7);
  }
  function withPreservedAlpha(previousHex, newHex6) {
    const alpha = (previousHex || "").length === 9 ? previousHex.slice(7) : "";
    return newHex6 + alpha;
  }

  // ---- Field bindings ----

  fontSelect.addEventListener("change", () => { draft.font = fontSelect.value; });
  sizeInput.addEventListener("input", () => { draft.size = Number(sizeInput.value) || draft.size; });
  spacingInput.addEventListener("input", () => { draft.letter_spacing = Number(spacingInput.value) || 0; });
  uppercaseCheck.addEventListener("change", () => { draft.uppercase = uppercaseCheck.checked; });
  colourText.addEventListener("input", () => { draft.colors.text = withPreservedAlpha(draft.colors.text, colourText.value); });
  colourActive.addEventListener("input", () => { draft.colors.active = withPreservedAlpha(draft.colors.active, colourActive.value); });
  colourOutline.addEventListener("input", () => { draft.colors.outline = withPreservedAlpha(draft.colors.outline, colourOutline.value); });
  colourBox.addEventListener("input", () => { draft.colors.box = withPreservedAlpha(draft.colors.box, colourBox.value); });

  // ---- Save / Save as / Duplicate / Delete / Reset ----

  function showSaveStatus(message, ok) {
    saveStatus.textContent = message;
    saveStatus.className = ok ? "ok" : "err";
  }
  function hideSaveStatus() {
    saveStatus.className = "";
    saveStatus.textContent = "";
  }

  async function saveAs(name) {
    if (!name || !name.trim()) return;
    const res = await fetch(`/api/styles/${encodeURIComponent(name.trim())}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showSaveStatus(body.detail || "Could not save this style.", false);
      return;
    }
    await loadStyles(name.trim());
    showSaveStatus("Saved.", true);
  }

  saveBtn.addEventListener("click", () => saveAs(selectedName));
  saveAsBtn.addEventListener("click", () => {
    const name = window.prompt("Save this style as:", selectedName + " copy");
    if (name) saveAs(name);
  });
  duplicateBtn.addEventListener("click", () => {
    if (!selectedName) return;
    const name = window.prompt("Duplicate as:", selectedName + " copy");
    if (name) saveAs(name);
  });

  deleteBtn.addEventListener("click", async () => {
    if (!selectedName) return;
    if (!window.confirm(`Delete "${selectedName}"? This can't be undone.`)) return;
    const res = await fetch(`/api/styles/${encodeURIComponent(selectedName)}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      window.alert(body.detail || "Could not delete this style.");
      return;
    }
    await loadStyles();
  });

  resetBtn.addEventListener("click", async () => {
    if (!selectedName) return;
    const res = await fetch(`/api/styles/${encodeURIComponent(selectedName)}?shipped_only=true`);
    if (!res.ok) return;
    const style = await res.json();
    draft = JSON.parse(JSON.stringify(style.definition));
    draft.name = selectedName;
    applyDraftToForm();
    showSaveStatus("Reset to the built-in version -- hit Save to keep it.", true);
  });

  // ---- Live preview (spec 7A.3, the centrepiece) ----

  function setPreviewStatus(message, kind) {
    previewStatus.className = "visible" + (kind === "err" ? " err" : "");
    previewStatus.innerHTML = kind === "busy"
      ? `<span class="spinner"></span><span>${escapeHtml(message)}</span>`
      : escapeHtml(message);
  }
  function clearPreviewStatus() {
    previewStatus.className = "";
    previewStatus.innerHTML = "";
  }

  renderBtn.addEventListener("click", async () => {
    const videoPath = previewPathInput.value.trim();
    const startSeconds = Number(previewTimeInput.value) || 0;
    if (!videoPath) {
      setPreviewStatus("Enter the video's file location first.", "err");
      return;
    }
    if (previewPollTimer) { clearInterval(previewPollTimer); previewPollTimer = null; }
    previewOutput.hidden = true;
    renderBtn.disabled = true;
    setPreviewStatus("Starting…", "busy");

    try {
      const res = await fetch("/api/styles/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_path: videoPath, start_seconds: startSeconds, style: draft }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Could not start the preview.");
      }
      const job = await res.json();
      pollPreview(job.id);
    } catch (err) {
      setPreviewStatus(err.message, "err");
      renderBtn.disabled = false;
    }
  });

  function phaseMessage(phase) {
    if (phase === "transcribing") return "Listening to your video…";
    if (phase === "rendering") return "Rendering your preview…";
    return "Working on your preview…";
  }

  function pollPreview(jobId) {
    previewPollTimer = setInterval(async () => {
      let job;
      try {
        const res = await fetch(`/api/styles/preview/${encodeURIComponent(jobId)}`);
        if (!res.ok) throw new Error("Lost track of the preview job.");
        job = await res.json();
      } catch (err) {
        clearInterval(previewPollTimer);
        previewPollTimer = null;
        renderBtn.disabled = false;
        setPreviewStatus(err.message, "err");
        return;
      }

      if (job.status === "done") {
        clearInterval(previewPollTimer);
        previewPollTimer = null;
        renderBtn.disabled = false;
        clearPreviewStatus();
        previewVideo.src = `/api/styles/preview/${encodeURIComponent(jobId)}/clip`;
        previewOutput.hidden = false;
        previewVideo.play().catch(() => {});
      } else if (job.status === "failed") {
        clearInterval(previewPollTimer);
        previewPollTimer = null;
        renderBtn.disabled = false;
        setPreviewStatus(job.error || "The preview failed to render.", "err");
      } else {
        setPreviewStatus(phaseMessage(job.phase), "busy");
      }
    }, 800);
  }

  // ---- Boot ----

  loadFonts();
  loadStyles();
})();
