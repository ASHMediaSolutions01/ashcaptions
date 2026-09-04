/* The style editor's live preview (spec 7A.3): which footage to render
   on -- a dropdown of recent jobs' videos, or "Other..." with a pasted
   path or Browse... -- the start time (defaulting to the first spoken
   second from the chosen job's .srt), and the render/poll flow.
   style_editor.js hands over a getter for the in-progress draft. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const sourceSelect = $("preview-source-select");
  const pathField = $("preview-path-field");
  const pathInput = $("preview-path-input");
  const browseBtn = $("preview-browse-btn");
  const timeInput = $("preview-time-input");
  const timeHint = $("preview-time-hint");
  const renderBtn = $("render-btn");
  const status = $("preview-status");
  const output = $("preview-output");
  const video = $("preview-video");

  const OTHER = "__other__";
  let jobsByPath = new Map(); // input_path -> job (newest per path)
  let pollTimer = null;
  let getDraft = () => null;

  function setStatus(message, kind) {
    status.className = "visible" + (kind === "err" ? " err" : "");
    status.innerHTML = "";
    if (kind === "busy") {
      const spin = document.createElement("span");
      spin.className = "spinner";
      status.appendChild(spin);
    }
    status.appendChild(document.createTextNode(message));
  }
  function clearStatus() { status.className = ""; status.innerHTML = ""; }

  // ---- sources ----

  function baseName(path) {
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  }

  async function loadSources() {
    let jobs = [];
    try {
      const res = await AshApi.request("/api/jobs");
      if (res.ok) jobs = await res.json();
    } catch (err) { /* the dropdown still offers Other... */ }
    jobsByPath = new Map();
    for (const job of jobs) {
      if (job.input_path && !jobsByPath.has(job.input_path)) jobsByPath.set(job.input_path, job);
    }
    sourceSelect.innerHTML = "";
    for (const [path, job] of jobsByPath) {
      const opt = document.createElement("option");
      opt.value = path;
      opt.textContent = job.options && job.options.client ? `${baseName(path)} (${job.options.client})` : baseName(path);
      opt.title = path;
      sourceSelect.appendChild(opt);
    }
    const other = document.createElement("option");
    other.value = OTHER;
    other.textContent = jobsByPath.size ? "Other…" : "Choose a file…";
    sourceSelect.appendChild(other);
    onSourceChanged();
  }

  function chosenPath() {
    return sourceSelect.value === OTHER ? pathInput.value.trim() : sourceSelect.value;
  }

  async function onSourceChanged() {
    const other = sourceSelect.value === OTHER;
    pathField.hidden = !other;
    if (other) { timeHint.textContent = "Pick a moment where someone is talking."; return; }
    const job = jobsByPath.get(sourceSelect.value);
    if (job) await defaultStartFrom(job);
  }

  // The first cue's start in the job's .srt: the first second with speech,
  // so the preview never lands on the intro silence.
  async function defaultStartFrom(job) {
    timeHint.textContent = "Pick a moment where someone is talking.";
    try {
      const res = await AshApi.request(`/api/jobs/${encodeURIComponent(job.id)}/srt`);
      if (!res.ok) return;
      const m = /(\d+):(\d+):(\d+)[,.](\d+)\s*-->/.exec(await res.text());
      if (!m) return;
      const seconds = Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000;
      timeInput.value = String(Math.floor(seconds * 2) / 2);
      timeHint.textContent = `Speech starts at ${timeInput.value} s in this video.`;
    } catch (err) { /* keep whatever was there */ }
  }

  sourceSelect.addEventListener("change", onSourceChanged);
  browseBtn.addEventListener("click", async () => {
    browseBtn.disabled = true;
    try {
      const res = await AshApi.request("/api/pick-file", { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't open the file dialog"));
      const body = await res.json();
      if (body.path) pathInput.value = body.path;
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    } finally {
      browseBtn.disabled = false;
    }
  });

  // ---- render / poll ----

  renderBtn.addEventListener("click", async () => {
    const videoPath = chosenPath();
    const startSeconds = Number(timeInput.value) || 0;
    if (!videoPath) {
      setStatus("Choose a video first.", "err");
      return;
    }
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    output.hidden = true;
    renderBtn.disabled = true;
    setStatus("Starting…", "busy");
    try {
      const res = await AshApi.request("/api/styles/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_path: videoPath, start_seconds: startSeconds, style: getDraft() }),
      });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not start the preview"));
      poll((await res.json()).id);
    } catch (err) {
      setStatus(err.message, "err");
      renderBtn.disabled = false;
    }
  });

  function phaseMessage(phase) {
    if (phase === "transcribing") return "Listening to the video…";
    if (phase === "rendering") return "Rendering the preview…";
    return "Working on the preview…";
  }

  function poll(jobId) {
    pollTimer = setInterval(async () => {
      let job;
      try {
        const res = await AshApi.request(`/api/styles/preview/${encodeURIComponent(jobId)}`);
        if (!res.ok) throw new Error("Lost track of the preview job.");
        job = await res.json();
      } catch (err) {
        clearInterval(pollTimer);
        pollTimer = null;
        renderBtn.disabled = false;
        setStatus(err.message, "err");
        return;
      }
      if (job.status === "done") {
        clearInterval(pollTimer);
        pollTimer = null;
        renderBtn.disabled = false;
        clearStatus();
        video.src = `/api/styles/preview/${encodeURIComponent(jobId)}/clip`;
        output.hidden = false;
        video.play().catch(() => {});
      } else if (job.status === "failed") {
        clearInterval(pollTimer);
        pollTimer = null;
        renderBtn.disabled = false;
        setStatus(job.error || "The preview failed to render.", "err");
      } else {
        setStatus(phaseMessage(job.phase), "busy");
      }
    }, 800);
  }

  window.AshEditorPreview = {
    init(options) {
      getDraft = options.getDraft;
      loadSources();
    },
  };
})();
