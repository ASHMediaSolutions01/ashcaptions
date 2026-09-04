/* Control page: choosing a video (Browse..., a pasted path, or an upload),
   the options, submitting, and the live queue feed. Card rendering and
   housekeeping live in queue.js; the Studio hand-off in studio_hook.js;
   the client field and glossary in clients.js. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const appError = $("app-error");
  const connectionBanner = $("connection-banner");
  const submitForm = $("submit-form");
  const pathInput = $("path-input");
  const browseBtn = $("browse-btn");
  const options = $("options");
  const advancedOptions = $("advanced-options");
  const dropzone = $("dropzone");
  const fileInput = $("file-input");
  const selectedFileEl = $("selected-file");
  const languageSelect = $("language-select");
  const dialectSelect = $("dialect-select");
  const presetSelect = $("preset-select");
  const burnInCheck = $("burn-in-check");
  const translateCheck = $("translate-check");
  const behindCheck = $("behind-check");
  const startBtn = $("start-btn");
  const cancelBtn = $("cancel-btn");
  const submitError = $("submit-error");
  const queueHealth = $("queue-health");
  const navStatus = $("nav-status");
  const startHere = $("start-here");

  const START_HERE_KEY = "ash.startHereDismissed";
  let languages = [];
  // { type: "path", value: "D:\...\clip.mp4" } or { type: "upload", value: File }
  let selectedSource = null;

  // ---- Page-level errors: said out loud at the top, never swallowed ----

  function showAppError(message) { appError.textContent = message; appError.hidden = false; }
  function clearAppError() { appError.hidden = true; appError.textContent = ""; }

  async function loadJson(url, what) {
    const res = await AshApi.request(url);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, `Couldn't load ${what}`));
    return res.json();
  }

  // ---- Caption style dropdown (spec 7A): the same library the style editor manages ----

  async function loadPresets() {
    let styles;
    try {
      styles = await loadJson("/api/styles", "the caption styles");
    } catch (err) {
      showAppError(`${err.message}. Refresh the page; if it keeps happening, restart ASH Captions.`);
      return;
    }
    presetSelect.innerHTML = "";
    for (const style of styles) {
      const opt = document.createElement("option");
      opt.value = style.name;
      // A shipped name with a saved local edit silently overrides it for
      // every job -- flagged here, where the consequence lands.
      opt.textContent = style.customized_locally ? `${style.name} (customized)` : style.shipped ? style.name : `${style.name} (custom)`;
      presetSelect.appendChild(opt);
    }
    const pop = styles.find((s) => s.name === "POP");
    presetSelect.value = pop ? pop.name : (styles[0] && styles[0].name);
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
      languages = await loadJson("/api/languages", "the language list");
    } catch (err) {
      showAppError(`${err.message}. Refresh the page; if it keeps happening, restart ASH Captions.`);
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

  // ---- Choosing the video ----
  // Browse... opens the real Windows dialog on this desktop; pasting a
  // path still works; an upload (Advanced) copies the file first.

  function baseName(path) {
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  }

  function setSource(source) {
    selectedSource = source;
    const chosen = Boolean(source);
    options.disabled = !chosen;
    advancedOptions.disabled = !chosen && !advancedOptionsOpen();
    startBtn.disabled = !chosen;
    cancelBtn.hidden = !chosen;
    submitError.hidden = true;
    if (source && source.type === "upload") {
      pathInput.value = "";
      pathInput.disabled = true;
      selectedFileEl.textContent = `Uploading a copy of ${source.value.name}`;
      selectedFileEl.hidden = false;
    } else {
      pathInput.disabled = false;
      selectedFileEl.hidden = true;
    }
  }
  // The upload dropzone lives inside Advanced, which must stay usable
  // before a file is chosen -- it is one of the ways to choose one.
  function advancedOptionsOpen() { return $("advanced-details").open; }
  $("advanced-details").addEventListener("toggle", () => {
    if (!selectedSource) advancedOptions.disabled = !advancedOptionsOpen();
  });

  function resetSelection() {
    pathInput.value = "";
    fileInput.value = "";
    setSource(null);
    advancedOptions.disabled = !advancedOptionsOpen();
  }

  pathInput.addEventListener("input", () => {
    const value = pathInput.value.trim();
    setSource(value ? { type: "path", value } : null);
  });

  browseBtn.addEventListener("click", async () => {
    browseBtn.disabled = true;
    const label = browseBtn.textContent;
    browseBtn.textContent = "Choosing…";
    try {
      const res = await AshApi.request("/api/pick-file", { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't open the file dialog"));
      const body = await res.json();
      if (body.path) {
        pathInput.value = body.path;
        setSource({ type: "path", value: body.path });
        startBtn.focus();
      }
      // Cancelled: nothing changes, nothing to say.
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    } finally {
      browseBtn.disabled = false;
      browseBtn.textContent = label;
    }
  });

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) setSource({ type: "upload", value: fileInput.files[0] });
  });
  ["dragenter", "dragover"].forEach((evt) => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); }));
  ["dragleave", "drop"].forEach((evt) => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag-over"); }));
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) setSource({ type: "upload", value: file });
  });
  cancelBtn.addEventListener("click", resetSelection);

  // Behind-the-speaker only applies to a burn; say so by ticking burn-in.
  behindCheck.addEventListener("change", () => { if (behindCheck.checked) burnInCheck.checked = true; });
  burnInCheck.addEventListener("change", () => { if (!burnInCheck.checked) behindCheck.checked = false; });

  // ---- Submit (a real <form>, so Enter in the path field starts the job) ----

  submitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedSource) return;
    startBtn.disabled = true;
    submitError.hidden = true;
    try {
      const res = selectedSource.type === "path" ? await submitByPath() : await submitByUpload();
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not start this job"));
      const job = await res.json().catch(() => null);
      AshStudio.noteSubmitted(job);
      AshClients.remember();
      AshNotify.requestOnce();
      resetSelection();
      AshClients.refresh();
      AshToast.show(job ? `${job.filename} is in the queue.` : "Job queued.", { kind: "ok", ms: 4000 });
      await refreshJobs();
    } catch (err) {
      submitError.textContent = err.message;
      submitError.hidden = false;
      startBtn.disabled = false;
    }
  });

  function submitByPath() {
    return AshApi.request("/api/jobs/by-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: selectedSource.value,
        language: languageSelect.value,
        dialect: dialectSelect.value || null,
        preset: presetSelect.value,
        burn_in: burnInCheck.checked,
        translate_to_english: translateCheck.checked,
        behind_speaker: behindCheck.checked,
        client: AshClients.value() || null,
      }),
    });
  }

  function submitByUpload() {
    const form = new FormData();
    form.append("file", selectedSource.value);
    form.append("language", languageSelect.value);
    if (dialectSelect.value) form.append("dialect", dialectSelect.value);
    form.append("preset", presetSelect.value);
    form.append("burn_in", burnInCheck.checked ? "true" : "false");
    form.append("translate_to_english", translateCheck.checked ? "true" : "false");
    form.append("behind_speaker", behindCheck.checked ? "true" : "false");
    if (AshClients.value()) form.append("client", AshClients.value());
    return AshApi.request("/api/jobs", { method: "POST", body: form });
  }

  // ---- Queue feed ----

  function renderJobs(jobs) {
    AshUpdates.setQueueBusy(
      (jobs || []).some((j) => j.status === "running") ? "A caption job is still running. Try again when the queue is clear." : null
    );
    AshQueue.render(jobs);
    AshStudio.onJobs(jobs);
    const running = (jobs || []).filter((j) => j.status === "running").length;
    const waiting = (jobs || []).filter((j) => j.status === "pending").length;
    navStatus.textContent = running ? `${running} working${waiting ? `, ${waiting} waiting` : ""}` : waiting ? `${waiting} waiting` : "";
    const done = (jobs || []).find((j) => j.status === "done");
    if (done && window.AshNav) AshNav.pointStudioAt(readLastStudio() || done.id);
  }

  function readLastStudio() {
    try { return localStorage.getItem("ash.lastStudioJob") || ""; } catch (err) { return ""; }
  }

  async function refreshJobs() {
    try {
      renderJobs(await loadJson("/api/jobs", "the job list"));
      clearAppError();
    } catch (err) {
      showAppError(`${err.message}. The queue shown may be out of date.`);
    }
  }

  // ---- Health line (from the `health` SSE event; null fields read as "unknown") ----

  let health = { worker_alive: null, lastPollAt: null, live: false };

  function applyHealth(payload) {
    const serverNow = Date.parse(payload.server_time);
    const clockOffset = Number.isNaN(serverNow) ? 0 : Date.now() - serverNow;
    const lastPoll = Date.parse(payload.last_watcher_poll);
    health.worker_alive = payload.worker_alive;
    health.lastPollAt = Number.isNaN(lastPoll) ? null : lastPoll + clockOffset;
    renderHealth();
  }

  function renderHealth() {
    const worker = health.worker_alive === true ? "running" : health.worker_alive === false ? "stopped" : "unknown";
    const parts = [`Worker: ${worker}`];
    if (health.lastPollAt) parts.push(`checked ${AshQueue.formatDuration(Date.now() - health.lastPollAt)} ago`);
    parts.push(health.live ? "live" : "not connected");
    queueHealth.textContent = parts.join(" · ");
    queueHealth.classList.toggle("bad", health.worker_alive === false || !health.live);
  }
  setInterval(renderHealth, 1000);

  // ---- Live updates: one EventSource for the life of the page ----

  let lostContactTimer = null;

  function connectEvents() {
    const source = new EventSource("/api/events");
    source.onopen = () => {
      if (lostContactTimer) { clearTimeout(lostContactTimer); lostContactTimer = null; }
      connectionBanner.hidden = true;
      health.live = true;
      renderHealth();
    };
    source.onmessage = (evt) => {
      try { renderJobs(JSON.parse(evt.data)); } catch (err) { /* ignore a malformed frame */ }
    };
    source.addEventListener("health", (evt) => {
      try { applyHealth(JSON.parse(evt.data)); } catch (err) { /* ignore a malformed frame */ }
    });
    source.onerror = () => {
      health.live = false;
      renderHealth();
      // EventSource retries on its own; only speak up if it stays down.
      if (!lostContactTimer) {
        lostContactTimer = setTimeout(() => {
          lostContactTimer = null;
          if (source.readyState !== EventSource.OPEN) connectionBanner.hidden = false;
        }, 2500);
      }
    };
  }

  // ---- First-run "Start here" card, dismissed for good ----

  function startHereDismissed() {
    try { return localStorage.getItem(START_HERE_KEY) === "1"; } catch (err) { return false; }
  }
  startHere.hidden = startHereDismissed();
  $("start-here-dismiss").addEventListener("click", () => {
    startHere.hidden = true;
    try { localStorage.setItem(START_HERE_KEY, "1"); } catch (err) { /* private mode */ }
  });

  // ---- Boot ----

  window.AshApp = { refreshJobs };
  setSource(null);
  advancedOptions.disabled = true;
  loadLanguages();
  loadPresets();
  refreshJobs();
  connectEvents();
  AshUpdates.checkForUpdate((message) => { queueHealth.title = message; });
})();
