(function () {
  "use strict";

  const appError = document.getElementById("app-error");
  const connectionBanner = document.getElementById("connection-banner");
  const submitForm = document.getElementById("submit-form");
  const pathInput = document.getElementById("path-input");
  const uploadDetails = document.getElementById("upload-details");
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const chooseBtn = document.getElementById("choose-btn");
  const optionsForm = document.getElementById("options-form");
  const selectedFileEl = document.getElementById("selected-file");
  const languageSelect = document.getElementById("language-select");
  const dialectSelect = document.getElementById("dialect-select");
  const presetSelect = document.getElementById("preset-select");
  const burnInCheck = document.getElementById("burn-in-check");
  const translateCheck = document.getElementById("translate-check");
  const startBtn = document.getElementById("start-btn");
  const cancelBtn = document.getElementById("cancel-btn");
  const submitError = document.getElementById("submit-error");
  const jobList = document.getElementById("job-list");
  const emptyQueue = document.getElementById("empty-queue");
  const queueHealth = document.getElementById("queue-health");

  let languages = [];
  // { type: "path", value: "D:\...\clip.mp4" } or { type: "upload", value: File }
  let selectedSource = null;

  // ---- Page-level errors: said out loud at the top, never swallowed ----

  function showAppError(message) {
    appError.textContent = message;
    appError.hidden = false;
  }

  function clearAppError() {
    appError.hidden = true;
    appError.textContent = "";
  }

  async function loadJson(url, what) {
    const res = await AshApi.request(url);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, `Couldn't load ${what}`));
    return res.json();
  }

  // ---- Caption style dropdown (spec 7A) ----
  // Populated from the same style library the style editor manages, so a
  // custom look designed there shows up here too -- not a hardcoded pair.

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
      // A style whose name matches a built-in but has a saved local edit
      // silently overrides it for every job (spec 7A) -- flagged here too,
      // not just in the style editor, since this is where the consequence
      // actually lands: picking "POP" here uses the customized version.
      opt.textContent = style.customized_locally
        ? `${style.name} (customized)`
        : style.shipped
        ? style.name
        : `${style.name} (custom)`;
      presetSelect.appendChild(opt);
    }
    // POP is the short-form default (spec 6); fall back to the first style.
    const pop = styles.find((s) => s.name === "POP");
    presetSelect.value = pop ? pop.name : (styles[0] && styles[0].name);
  }

  // ---- Language / dialect dropdowns ----

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
    // Sensible default: English if present, else the first language.
    const english = languages.find((l) => l.code === "en");
    languageSelect.value = english ? english.code : (languages[0] && languages[0].code);
    renderDialects();
  }

  languageSelect.addEventListener("change", renderDialects);

  // ---- File selection ----
  // Two ways in: paste a path to a file already on this PC (primary -- no
  // copy, works instantly on multi-GB footage), or upload a copy (secondary,
  // tucked behind the "Or upload a copy instead" disclosure).

  function baseName(path) {
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  }

  function showOptionsForPath(path) {
    selectedSource = { type: "path", value: path };
    selectedFileEl.textContent = baseName(path);
    optionsForm.classList.add("visible");
    submitError.style.display = "none";
  }

  function showOptionsForUpload(file) {
    selectedSource = { type: "upload", value: file };
    selectedFileEl.textContent = file.name;
    optionsForm.classList.add("visible");
    submitError.style.display = "none";
  }

  function resetSelection() {
    selectedSource = null;
    pathInput.value = "";
    fileInput.value = "";
    uploadDetails.open = false;
    optionsForm.classList.remove("visible");
  }

  pathInput.addEventListener("input", () => {
    const value = pathInput.value.trim();
    if (value) {
      showOptionsForPath(value);
    } else if (selectedSource && selectedSource.type === "path") {
      optionsForm.classList.remove("visible");
      selectedSource = null;
    }
  });

  chooseBtn.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("click", (e) => {
    if (e.target === chooseBtn) return;
    fileInput.click();
  });
  dropzone.addEventListener("keydown", (e) => {
    if (e.target !== dropzone) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) showOptionsForUpload(fileInput.files[0]);
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag-over");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-over");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) showOptionsForUpload(file);
  });

  cancelBtn.addEventListener("click", resetSelection);

  // ---- Submit ----
  // A real <form>, so Enter in the path field starts the job.

  submitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedSource) return;
    startBtn.disabled = true;
    submitError.style.display = "none";

    try {
      const res =
        selectedSource.type === "path" ? await submitByPath() : await submitByUpload();
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not start this job"));
      resetSelection();
      await refreshJobs();
    } catch (err) {
      submitError.textContent = err.message;
      submitError.style.display = "block";
    } finally {
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
    return AshApi.request("/api/jobs", { method: "POST", body: form });
  }

  // ---- Queue rendering ----

  const STATUS_LABEL = { pending: "Waiting", running: "Working", done: "Done", failed: "Failed" };
  const STAGE_LABEL = {
    extract: "Extracting audio",
    transcribe: "Transcribing",
    translate: "Translating to English",
    postprocess: "Cleaning up the text",
    write: "Writing captions",
    cards_and_write: "Writing captions",
    burn: "Burning captions in",
  };

  // What the job is doing right now. Prefers the queue's own `stage`;
  // without one, reads it off the progress budget the pipeline uses
  // (extract ~5%, transcribe to ~60%, then writing, then burn from ~85%).
  function stageLabel(job) {
    if (job.stage) return STAGE_LABEL[job.stage] || job.stage;
    const pct = (job.progress || 0) * 100;
    if (pct < 5) return "Extracting audio";
    if (pct < 60) return "Transcribing";
    if (job.options && job.options.burn_in && pct >= 85) return "Burning captions in";
    return "Writing captions";
  }

  function formatDuration(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return `${s} s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    return `${h} h ${m % 60} min`;
  }

  function parseTime(iso) {
    const t = iso ? Date.parse(iso) : NaN;
    return Number.isNaN(t) ? null : t;
  }

  // Per job: [label, liveSinceTimestamp|null]. Live ones tick every second.
  function elapsedFor(job) {
    const started = parseTime(job.started_at) || parseTime(job.created_at);
    const created = parseTime(job.created_at);
    const updated = parseTime(job.updated_at);
    if (job.status === "running") return started ? ["Running for", started] : ["", null];
    if (job.status === "pending") return created ? ["Waiting for", created] : ["", null];
    if (started && updated && updated >= started) {
      const took = formatDuration(updated - started);
      return [job.status === "done" ? `Finished in ${took}` : `Failed after ${took}`, null];
    }
    return ["", null];
  }

  function renderJobs(jobs) {
    // Kept live off the same job snapshot the queue section already
    // renders from, so the Update button proactively disables itself the
    // moment a job starts running, without a second poll of its own.
    AshUpdates.setQueueBusy(
      (jobs || []).some((j) => j.status === "running")
        ? "A caption job is still running. Try again when the queue is clear."
        : null
    );

    if (!jobs || jobs.length === 0) {
      emptyQueue.style.display = "block";
      jobList.innerHTML = "";
      return;
    }
    emptyQueue.style.display = "none";
    jobList.innerHTML = "";
    for (const job of jobs) {
      jobList.appendChild(renderJob(job));
    }
    tickClocks();
  }

  function renderJob(job) {
    const el = document.createElement("div");
    el.className = "job " + job.status;

    const pct = job.status === "done" ? 100 : Math.round((job.progress || 0) * 100);
    const label = STATUS_LABEL[job.status] || job.status;
    const opts = job.options || {};
    const meta = [opts.language, opts.dialect, opts.preset, opts.burn_in ? "burn-in" : null, opts.translate_to_english ? "+ English" : null]
      .filter(Boolean)
      .join(" · ");

    let stageText = "";
    if (job.status === "running") stageText = `${stageLabel(job)} · ${pct}%`;
    else if (job.status === "pending") stageText = "Waiting in the queue";
    else if (job.status === "done") stageText = "Done · 100%";
    else if (job.status === "failed") stageText = "Failed";

    const [elapsedLabel, since] = elapsedFor(job);

    el.innerHTML = `
      <div class="job-top">
        <div class="job-name">${escapeHtml(job.filename)}</div>
        <div class="badge ${job.status}">${label}</div>
      </div>
      <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
        <div class="progress-fill" style="width:${pct}%"></div>
      </div>
      <div class="job-status-line">
        <span class="job-stage">${escapeHtml(stageText)}</span>
        <span class="job-elapsed" ${since ? `data-since="${since}" data-label="${escapeHtml(elapsedLabel)}"` : ""}>${escapeHtml(elapsedLabel)}</span>
      </div>
      <div class="job-meta">${escapeHtml(meta)}</div>
    `;

    if (job.status === "failed") {
      const errBox = document.createElement("div");
      errBox.className = "job-error";
      errBox.textContent = job.error || "Something went wrong.";
      el.appendChild(errBox);

      const actions = document.createElement("div");
      actions.className = "job-actions";
      const retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.className = "btn secondary";
      retryBtn.textContent = "Retry";
      retryBtn.addEventListener("click", () => retryJob(job.id, retryBtn));
      actions.appendChild(retryBtn);
      el.appendChild(actions);
    }

    return el;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  async function retryJob(jobId, btn) {
    btn.disabled = true;
    try {
      const res = await AshApi.request(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not retry this job"));
      await refreshJobs();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
  }

  async function refreshJobs() {
    try {
      renderJobs(await loadJson("/api/jobs", "the job list"));
      clearAppError();
    } catch (err) {
      showAppError(`${err.message}. The queue shown may be out of date.`);
    }
  }

  // ---- Clocks: "Running for 23 min" / "last check 3 s ago" ----
  // Ticked client-side once a second between server frames -- a
  // transcription can go minutes without a progress change, and a
  // bar that hasn't moved needs to say plainly that time is passing.

  function tickClocks() {
    const now = Date.now();
    for (const el of jobList.querySelectorAll(".job-elapsed[data-since]")) {
      el.textContent = `${el.dataset.label} ${formatDuration(now - Number(el.dataset.since))}`;
    }
    renderHealth(now);
  }
  setInterval(tickClocks, 1000);

  // ---- Health line (from the `health` SSE event; null fields read as "unknown") ----

  let health = { worker_alive: null, lastPollAt: null, live: false };

  function applyHealth(payload) {
    const serverNow = parseTime(payload.server_time);
    const clockOffset = serverNow ? Date.now() - serverNow : 0; // guards against skew
    const lastPoll = parseTime(payload.last_watcher_poll);
    health.worker_alive = payload.worker_alive;
    health.lastPollAt = lastPoll ? lastPoll + clockOffset : null;
    renderHealth(Date.now());
  }

  function renderHealth(now) {
    const worker =
      health.worker_alive === true ? "running" : health.worker_alive === false ? "stopped" : "unknown";
    const parts = [`Worker: ${worker}`];
    if (health.lastPollAt) parts.push(`last check ${formatDuration(now - health.lastPollAt)} ago`);
    parts.push(health.live ? "live" : "not connected");
    queueHealth.textContent = parts.join(" · ");
    queueHealth.classList.toggle("bad", health.worker_alive === false || !health.live);
  }

  // ---- Live updates ----
  // One EventSource for the life of the page. The server heartbeats every
  // second so an hour of transcription silence never looks like a dead
  // connection; if it really does drop, the browser reconnects itself and
  // the banner says so in the meantime.

  let lostContactTimer = null;

  function connectEvents() {
    const source = new EventSource("/api/events");
    source.onopen = () => {
      if (lostContactTimer) { clearTimeout(lostContactTimer); lostContactTimer = null; }
      connectionBanner.hidden = true;
      health.live = true;
      renderHealth(Date.now());
    };
    source.onmessage = (evt) => {
      try {
        renderJobs(JSON.parse(evt.data));
      } catch (err) {
        // ignore malformed frame
      }
    };
    source.addEventListener("health", (evt) => {
      try {
        applyHealth(JSON.parse(evt.data));
      } catch (err) {
        // ignore malformed frame
      }
    });
    source.onerror = () => {
      health.live = false;
      renderHealth(Date.now());
      // EventSource retries on its own (retry: 2000 from the server). Only
      // say something if it hasn't come back promptly -- a single blip
      // during a reconnect shouldn't flash a scary banner.
      if (!lostContactTimer) {
        lostContactTimer = setTimeout(() => {
          lostContactTimer = null;
          if (source.readyState !== EventSource.OPEN) connectionBanner.hidden = false;
        }, 2500);
      }
    };
  }

  // ---- Boot ----

  loadLanguages();
  loadPresets();
  refreshJobs();
  connectEvents();
  AshUpdates.checkForUpdate((message) => { queueHealth.title = message; });
})();
