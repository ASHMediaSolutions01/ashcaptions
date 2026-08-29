(function () {
  "use strict";

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

  let languages = [];
  let selectedFile = null;

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

  function showOptionsFor(file) {
    selectedFile = file;
    selectedFileEl.textContent = file.name;
    optionsForm.classList.add("visible");
    submitError.style.display = "none";
  }

  function resetSelection() {
    selectedFile = null;
    fileInput.value = "";
    optionsForm.classList.remove("visible");
  }

  chooseBtn.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("click", (e) => {
    if (e.target === chooseBtn) return;
    fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) showOptionsFor(fileInput.files[0]);
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
    if (file) showOptionsFor(file);
  });

  cancelBtn.addEventListener("click", resetSelection);

  // ---- Submit ----

  startBtn.addEventListener("click", async () => {
    if (!selectedFile) return;
    startBtn.disabled = true;
    submitError.style.display = "none";

    const form = new FormData();
    form.append("file", selectedFile);
    form.append("language", languageSelect.value);
    if (dialectSelect.value) form.append("dialect", dialectSelect.value);
    form.append("preset", presetSelect.value);
    form.append("burn_in", burnInCheck.checked ? "true" : "false");
    form.append("translate_to_english", translateCheck.checked ? "true" : "false");

    try {
      const res = await fetch("/api/jobs", { method: "POST", body: form });
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

  // ---- Queue rendering ----

  const STATUS_LABEL = { pending: "Waiting", running: "Working", done: "Done", failed: "Failed" };

  function renderJobs(jobs) {
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

  // ---- Boot ----

  loadLanguages();
  refreshJobs();
  connectEvents();
})();
