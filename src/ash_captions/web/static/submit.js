/* The drawer: choosing videos (Browse... for one or several, a pasted
   path, or files dropped anywhere on the drawer), the language and the
   client, and starting them all with one click. Every job goes out with
   the same language, dialect, client and speaker-names choice; the look,
   the reel, behind-the-speaker and the burn are decided in the Studio.
   Loaded before app.js, which calls AshSubmit.init(). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const form = $("submit-form");
  const drawer = $("drawer");
  const dropzone = $("dropzone");
  const browseBtn = $("browse-btn");
  const fileInput = $("file-input");
  const pathInput = $("path-input");
  const chosenList = $("chosen-list");
  const languageSelect = $("language-select");
  const dialectSelect = $("dialect-select");
  const speakersCheck = $("speakers-check");
  const startBtn = $("start-btn");
  const cancelBtn = $("cancel-btn");
  const moreBtn = $("drawer-more");
  const submitError = $("submit-error");

  let languages = [];
  // Each entry: { type: "path", value: "D:\...\clip.mp4", name } or
  // { type: "upload", value: File, name }. Kept in the order chosen.
  let chosen = [];
  let submitting = false;

  function baseName(path) {
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  }

  // "English (US)" for "en-US", "Spanish" for "es": the cards show what the
  // drawer said, not the code behind it. The code itself when unknown.
  function labelFor(code) {
    if (!code) return "";
    for (const lang of languages) {
      if (lang.code === code) return lang.label;
      const d = (lang.dialects || []).find((x) => x.code === code);
      if (d) return d.label;
    }
    return code;
  }

  // ---- Language / dialect ----

  function renderDialects() {
    const lang = languages.find((l) => l.code === languageSelect.value);
    dialectSelect.innerHTML = "";
    const dialects = (lang && lang.dialects) || [];
    if (dialects.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "Standard";
      dialectSelect.appendChild(opt);
      dialectSelect.disabled = true;
      return;
    }
    dialectSelect.disabled = false;
    for (const d of dialects) {
      const opt = document.createElement("option");
      opt.value = d.code;
      opt.textContent = d.label;
      dialectSelect.appendChild(opt);
    }
  }

  async function loadLanguages() {
    try {
      languages = await AshApp.loadJson("/api/languages", "the language list");
    } catch (err) {
      AshApp.showAppError(`${err.message}. Refresh the page; if it keeps happening, restart ASH Captions.`);
      return;
    }
    languageSelect.innerHTML = "";
    for (const lang of languages) {
      const opt = document.createElement("option");
      opt.value = lang.code;
      opt.textContent = lang.label;
      languageSelect.appendChild(opt);
    }
    const english = languages.find((l) => l.code === "en");
    languageSelect.value = english ? english.code : (languages[0] && languages[0].code);
    renderDialects();
  }
  languageSelect.addEventListener("change", renderDialects);

  // ---- The chosen list ----

  function sameSource(a, b) {
    if (a.type !== b.type) return false;
    if (a.type === "path") return a.value.toLowerCase() === b.value.toLowerCase();
    return a.value.name === b.value.name && a.value.size === b.value.size;
  }

  function add(source) {
    if (chosen.some((s) => sameSource(s, source))) {
      AshToast.show(`${source.name} is already in the list.`, { ms: 3000 });
      return;
    }
    chosen.push(source);
    renderChosen();
  }

  function removeAt(index) {
    chosen.splice(index, 1);
    renderChosen();
  }

  function clearChosen() {
    chosen = [];
    fileInput.value = "";
    pathInput.value = "";
    renderChosen();
  }

  function renderChosen() {
    chosenList.innerHTML = "";
    chosenList.hidden = chosen.length === 0;
    chosen.forEach((source, i) => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "chosen-name";
      name.textContent = source.name;
      name.title = source.type === "path" ? source.value : "A copy of this file will be uploaded first";
      const kind = document.createElement("span");
      kind.className = "chosen-kind";
      kind.textContent = source.type === "upload" ? "copy" : "";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn small quiet chosen-remove";
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove ${source.name}`);
      remove.addEventListener("click", () => removeAt(i));
      li.append(name, kind, remove);
      chosenList.appendChild(li);
    });
    const n = chosen.length;
    startBtn.disabled = n === 0 || submitting;
    startBtn.textContent = n > 1 ? `Start ${n} videos` : "Start captioning";
    cancelBtn.hidden = n === 0;
    submitError.hidden = true;
  }

  // ---- Choosing ----

  // A pasted path counts as chosen the moment the field is left or Start
  // is clicked, not only on Enter -- nothing should be lost for want of a key.
  function takePath() {
    const value = pathInput.value.trim().replace(/^"(.*)"$/, "$1");
    if (!value) return;
    add({ type: "path", value, name: baseName(value) });
    pathInput.value = "";
  }
  pathInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); takePath(); }
  });
  pathInput.addEventListener("change", takePath);

  browseBtn.addEventListener("click", async () => {
    browseBtn.disabled = true;
    const label = browseBtn.textContent;
    browseBtn.textContent = "Choosing…";
    try {
      const res = await AshApi.request("/api/pick-files", { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't open the file dialog"));
      const body = await res.json();
      for (const path of body.paths || []) add({ type: "path", value: path, name: baseName(path) });
      if ((body.paths || []).length) startBtn.focus();
      // Cancelled: nothing changes, nothing to say.
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    } finally {
      browseBtn.disabled = false;
      browseBtn.textContent = label;
    }
  });

  function addFiles(files) {
    for (const file of files || []) add({ type: "upload", value: file, name: file.name });
  }
  fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
  // Clicking the box is Browse: the native picker reads the file in place.
  // The hidden file input only serves what is dropped.
  dropzone.addEventListener("click", (e) => { if (e.target === dropzone || e.target.classList.contains("drop-text")) browseBtn.click(); });

  // The whole drawer is the drop target, not just the dashed box: a file
  // dragged in from Explorer lands wherever the hand was.
  let dragDepth = 0;
  drawer.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth += 1; drawer.classList.add("drag-over"); });
  drawer.addEventListener("dragover", (e) => { e.preventDefault(); });
  drawer.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (dragDepth === 0) drawer.classList.remove("drag-over"); });
  drawer.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    drawer.classList.remove("drag-over");
    addFiles(e.dataTransfer && e.dataTransfer.files);
  });
  cancelBtn.addEventListener("click", clearChosen);

  // ---- Submit: every chosen video, one after the other ----

  function common() {
    return {
      language: languageSelect.value,
      dialect: dialectSelect.value || null,
      client: AshClients.value() || null,
      speaker_labels: speakersCheck.checked,
    };
  }

  function submitByPath(source) {
    return AshApi.request("/api/jobs/by-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: source.value, ...common() }),
    });
  }

  function submitByUpload(source) {
    const fields = common();
    const body = new FormData();
    body.append("file", source.value);
    body.append("language", fields.language);
    if (fields.dialect) body.append("dialect", fields.dialect);
    if (fields.client) body.append("client", fields.client);
    body.append("speaker_labels", fields.speaker_labels ? "true" : "false");
    return AshApi.request("/api/jobs", { method: "POST", body });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    takePath();
    if (chosen.length === 0 || submitting) return;
    submitting = true;
    startBtn.disabled = true;
    submitError.hidden = true;
    const batch = chosen.length > 1;
    const started = [];
    const failed = [];
    for (const source of chosen) {
      startBtn.textContent = batch ? `Starting ${started.length + failed.length + 1} of ${chosen.length}…` : "Starting…";
      try {
        const res = source.type === "path" ? await submitByPath(source) : await submitByUpload(source);
        if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not start this job"));
        const job = await res.json().catch(() => null);
        started.push(job);
        AshStudio.noteSubmitted(job, { batch });
      } catch (err) {
        failed.push({ source, message: err.message });
      }
    }
    submitting = false;
    if (started.length) {
      AshClients.remember();
      AshNotify.requestOnce();
      AshClients.refresh();
      const first = started[0];
      AshToast.show(started.length === 1 && first ? `${first.filename} is in the queue.` : `${started.length} videos are in the queue.`, { kind: "ok", ms: 4000 });
    }
    // What failed stays in the list, so it can be fixed and started again.
    chosen = failed.map((f) => f.source);
    renderChosen();
    if (failed.length) {
      submitError.textContent = failed.length === 1 ? `${failed[0].source.name}: ${failed[0].message}` : failed.map((f) => `${f.source.name}: ${f.message}`).join(" ");
      submitError.hidden = false;
    }
    if (started.length && window.AshApp) AshApp.refreshJobs();
  });

  // On a narrow window the drawer is a bar: More opens the rest of it.
  moreBtn.addEventListener("click", () => {
    const open = drawer.classList.toggle("expanded");
    moreBtn.setAttribute("aria-expanded", open ? "true" : "false");
    moreBtn.textContent = open ? "Less" : "More";
  });

  function init() {
    renderChosen();
    loadLanguages();
  }

  window.AshSubmit = { init, add, labelFor };
})();
