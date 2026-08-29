(function () {
  "use strict";

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
  const updateBanner = document.getElementById("update-banner");
  const updateBannerDetail = document.getElementById("update-banner-detail");
  const updateBannerReason = document.getElementById("update-banner-reason");
  const updateNowBtn = document.getElementById("update-now-btn");

  let languages = [];
  // { type: "path", value: "D:\...\clip.mp4" } or { type: "upload", value: File }
  let selectedSource = null;
  let queueBusyReason = null; // set from the live job list; overrides the server's snapshot reason

  // ---- Caption style dropdown (spec 7A) ----
  // Populated from the same style library the style editor manages, so a
  // custom look designed there shows up here too -- not a hardcoded pair.

  async function loadPresets() {
    const res = await fetch("/api/styles");
    const styles = await res.json();
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
    const res = await fetch("/api/languages");
    languages = await res.json();
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

  startBtn.addEventListener("click", async () => {
    if (!selectedSource) return;
    startBtn.disabled = true;
    submitError.style.display = "none";

    try {
      const res =
        selectedSource.type === "path" ? await submitByPath() : await submitByUpload();
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Could not start this job.");
      }
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
    return fetch("/api/jobs/by-path", {
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
    return fetch("/api/jobs", { method: "POST", body: form });
  }

  // ---- Queue rendering ----

  const STATUS_LABEL = { pending: "Waiting", running: "Working", done: "Done", failed: "Failed" };

  function renderJobs(jobs) {
    // Kept live off the same job snapshot the queue section already
    // renders from, so the Update button proactively disables itself the
    // moment a job starts running, without a second poll of its own.
    queueBusyReason = (jobs || []).some((j) => j.status === "running")
      ? "A caption job is still running. Try again when the queue is clear."
      : null;
    updateUpdateButtonState();

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
  }

  function renderJob(job) {
    const el = document.createElement("div");
    el.className = "job " + job.status;

    const pct = Math.round((job.progress || 0) * 100);
    const label = STATUS_LABEL[job.status] || job.status;
    const opts = job.options || {};
    const meta = [opts.language, opts.dialect, opts.preset].filter(Boolean).join(" · ");

    el.innerHTML = `
      <div class="job-top">
        <div class="job-name">${escapeHtml(job.filename)}</div>
        <div class="badge ${job.status}">${label}</div>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:${job.status === 'done' ? 100 : pct}%"></div></div>
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
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
      if (!res.ok) throw new Error("Could not retry this job.");
      await refreshJobs();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
  }

  async function refreshJobs() {
    const res = await fetch("/api/jobs");
    renderJobs(await res.json());
  }

  // ---- Live updates ----

  function connectEvents() {
    const source = new EventSource("/api/events");
    source.onmessage = (evt) => {
      try {
        renderJobs(JSON.parse(evt.data));
      } catch (err) {
        // ignore malformed frame
      }
    };
    source.onerror = () => {
      // EventSource retries automatically; nothing to do here.
    };
  }

  // ---- In-app updates (spec 11.4) ----
  // The click on "Update now" IS the consent -- deliberately no confirmation
  // dialog here (a second "are you sure?" just trains people to click
  // through unread). Applying restarts the app, which is why the button
  // says so right next to itself rather than behind a dialog.

  function formatMegabytes(bytes) {
    return `${Math.round(bytes / 1024 / 1024)} MB`;
  }

  function updateUpdateButtonState() {
    if (updateBanner.hidden) return;
    updateNowBtn.disabled = !!queueBusyReason;
    updateBannerReason.hidden = !queueBusyReason;
    updateBannerReason.textContent = queueBusyReason || "";
  }

  async function checkForUpdate() {
    const res = await fetch("/api/update");
    if (!res.ok) return;
    const info = await res.json();
    if (!info) return;

    updateBannerDetail.textContent =
      `Version ${info.version} (${formatMegabytes(info.size_bytes)})` + (info.notes ? ` -- ${info.notes}` : "");
    if (info.blocked_reason) queueBusyReason = info.blocked_reason;
    updateBanner.hidden = false;
    updateUpdateButtonState();
  }

  updateNowBtn.addEventListener("click", async () => {
    updateNowBtn.disabled = true;
    updateBannerReason.hidden = false;
    updateBannerReason.textContent = "Starting the update…";

    try {
      const res = await fetch("/api/update/apply", { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Could not start the update.");
      }
      const job = await res.json();
      pollUpdateApply(job.id);
    } catch (err) {
      updateBannerReason.textContent = err.message;
      updateNowBtn.disabled = !!queueBusyReason;
    }
  });

  const UPDATE_PHASE_LABEL = {
    pending: "Starting the update…",
    downloading: "Downloading the update…",
    applying: "Applying the update…",
  };

  function pollUpdateApply(jobId) {
    const timer = setInterval(async () => {
      let job;
      try {
        const res = await fetch(`/api/update/apply/${encodeURIComponent(jobId)}`);
        if (!res.ok) throw new Error("Lost track of the update.");
        job = await res.json();
      } catch (err) {
        clearInterval(timer);
        updateBannerReason.textContent = err.message;
        updateNowBtn.disabled = !!queueBusyReason;
        return;
      }

      if (job.status === "done") {
        clearInterval(timer);
        updateBannerReason.textContent = "Update applied -- the app is restarting…";
        waitForRestartThenReload();
      } else if (job.status === "failed") {
        clearInterval(timer);
        updateBannerReason.textContent = job.error || "The update failed.";
        updateNowBtn.disabled = !!queueBusyReason;
      } else {
        updateBannerReason.textContent = UPDATE_PHASE_LABEL[job.status] || "Working…";
      }
    }, 1000);
  }

  function waitForRestartThenReload() {
    // The app process is about to exit and relaunch (spec 11.4) -- this
    // page's own connection will drop. Poll for it to come back rather
    // than making the editor remember to refresh manually.
    const timer = setInterval(async () => {
      try {
        const res = await fetch("/api/jobs", { cache: "no-store" });
        if (res.ok) {
          clearInterval(timer);
          window.location.reload();
        }
      } catch (err) {
        // still restarting; keep waiting
      }
    }, 2000);
  }

  // ---- Boot ----

  loadLanguages();
  loadPresets();
  refreshJobs();
  connectEvents();
  checkForUpdate();
})();
