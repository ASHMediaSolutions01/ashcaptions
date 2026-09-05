/* Export everywhere, named honestly (v0.6 §3). "Export" appears exactly
   once per surface and always means the finished burned-in file; every
   other button that produces an MP4 is explicitly a preview. This is that
   one control: `window.AshExport.mount(jobId, container)` drops a small
   "Export" menu into `container` (default `#export`) offering, with sizes,
   the burned video, the .srt, the .ass, the transcript .txt, and the
   English .en.srt when the job has one -- real downloads through the new
   GET /api/jobs/{id}/files/{name} route.

   Mounted from three places: this file's own end-of-page self-mount on the
   Studio page (`#export`), one call per finished row from queue.js, and
   (per the v0.6 interfaces table) an explicit `AshExport.mount(jobId)` call
   from the Styles page once track D wires it up -- `container` there
   defaults to `#export`, so that one-argument call matches the documented
   contract exactly.

   No burned video yet: the video row queues the burn (POST
   /api/jobs/{id}/burn) instead of downloading, and shows progress while it
   runs. The queue already keeps working if you navigate away; this is
   what finally says so, and it checks GET /api/jobs on mount for a burn
   already queued for this job so that promise holds across a reload too,
   not just within one page view. */
(function () {
  "use strict";

  // Pulls in its own stylesheet, at the same version-stamped URL this
  // script was loaded from -- so studio.html and index.html need only the
  // one <script> line each, no matching <link> to remember to add too.
  const ownScript = document.currentScript;
  if (ownScript && !document.querySelector('link[href^="/static/export.css"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = ownScript.src.replace(/export\.js/, "export.css");
    document.head.appendChild(link);
  }

  const POLL_JOB_MS = 2000; // while the source job itself isn't done yet
  const POLL_BURN_MS = 1500; // while a queued burn is pending/running

  function api(jobId, suffix) {
    return `/api/jobs/${encodeURIComponent(jobId)}${suffix || ""}`;
  }

  async function getJson(url) {
    const res = await AshApi.request(url);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't load that"));
    return res.json();
  }

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB"];
    let value = bytes / 1024;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
  }

  // ---- picking the right file out of GET /files for each category ----
  // Mirrors routes_studio.py's _burned_output/_transcript_srt exactly, so
  // the same job answers the same way here as it does in the Studio.

  function pickVideo(outputs) {
    return outputs.find((f) => f.name.toLowerCase().endsWith(".captioned.mp4"))
      || outputs.find((f) => f.name.toLowerCase().endsWith(".mp4"))
      || null;
  }
  function pickSrt(outputs) {
    return outputs.find((f) => f.name.toLowerCase().endsWith(".srt") && !f.name.toLowerCase().endsWith(".en.srt")) || null;
  }
  function pickEnSrt(outputs) {
    return outputs.find((f) => f.name.toLowerCase().endsWith(".en.srt")) || null;
  }
  function pickAss(outputs) {
    return outputs.find((f) => f.name.toLowerCase().endsWith(".ass")) || null;
  }
  function pickTxt(outputs) {
    return outputs.find((f) => f.name.toLowerCase().endsWith(".txt")) || null;
  }

  // ---- one mounted instance ----

  function mountOne(jobId, container) {
    if (!container) return;
    container.innerHTML = "";
    container.className = "export";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn small export-toggle";
    toggle.textContent = "Export";
    toggle.disabled = true;
    const menu = document.createElement("div");
    menu.className = "export-menu";
    menu.hidden = true;
    container.append(toggle, menu);

    let job = null;
    let outputs = [];
    let burnPollTimer = null;
    let jobPollTimer = null;

    function clearTimers() {
      if (burnPollTimer) { clearTimeout(burnPollTimer); burnPollTimer = null; }
      if (jobPollTimer) { clearTimeout(jobPollTimer); jobPollTimer = null; }
    }

    toggle.addEventListener("click", () => { menu.hidden = !menu.hidden; });
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target)) menu.hidden = true;
    });

    function row(label, file) {
      const item = document.createElement("div");
      item.className = "export-item";
      const a = document.createElement("a");
      a.className = "export-link";
      a.href = api(jobId, `/files/${encodeURIComponent(file.name)}`);
      a.download = file.name;
      a.textContent = label;
      const size = document.createElement("span");
      size.className = "export-size";
      size.textContent = formatSize(file.size_bytes);
      item.append(a, size);
      return item;
    }

    function pendingRow(text, subtext) {
      const item = document.createElement("div");
      item.className = "export-item export-pending";
      const label = document.createElement("span");
      label.textContent = text;
      item.appendChild(label);
      if (subtext) {
        const hint = document.createElement("div");
        hint.className = "export-hint";
        hint.textContent = subtext;
        item.appendChild(hint);
      }
      return item;
    }

    function progressRow(pct) {
      const item = document.createElement("div");
      item.className = "export-item export-progress";
      const label = document.createElement("span");
      label.textContent = `Exporting the video… ${Math.round(pct * 100)}%`;
      const track = document.createElement("div");
      track.className = "progress-track";
      const fill = document.createElement("div");
      fill.className = "progress-fill live";
      fill.style.width = `${Math.round(pct * 100)}%`;
      track.appendChild(fill);
      const hint = document.createElement("div");
      hint.className = "export-hint";
      hint.textContent = "This keeps running if you navigate away or close this page.";
      item.append(label, track, hint);
      return item;
    }

    function queueBurnRow() {
      const item = document.createElement("div");
      item.className = "export-item";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn small";
      btn.textContent = "Queue the burn";
      btn.addEventListener("click", () => queueBurn());
      item.appendChild(btn);
      return item;
    }

    function render() {
      menu.innerHTML = "";
      if (!job) { toggle.disabled = true; return; }
      if (job.status !== "done") {
        menu.appendChild(pendingRow("Still captioning", "Files will be ready to export once the job finishes."));
        toggle.disabled = true;
        return;
      }
      toggle.disabled = false;
      const video = pickVideo(outputs);
      if (video) menu.appendChild(row("Video with captions burned in", video));
      else menu.appendChild(queueBurnRow());
      const srt = pickSrt(outputs);
      if (srt) menu.appendChild(row("Subtitles (.srt)", srt));
      const ass = pickAss(outputs);
      if (ass) menu.appendChild(row("Styled subtitles (.ass)", ass));
      const txt = pickTxt(outputs);
      if (txt) menu.appendChild(row("Transcript (.txt)", txt));
      const enSrt = pickEnSrt(outputs);
      if (enSrt) menu.appendChild(row("English subtitles (.en.srt)", enSrt));
    }

    function showBurnProgress(burnJob) {
      menu.innerHTML = "";
      menu.appendChild(progressRow(burnJob.progress || 0));
    }

    async function refreshOutputs() {
      try {
        outputs = await getJson(api(jobId, "/files"));
      } catch (err) {
        outputs = [];
      }
      render();
    }

    async function pollBurn(burnJobId) {
      let current;
      try {
        current = await getJson(api(burnJobId));
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
        await refreshOutputs();
        return;
      }
      if (current.status === "done") {
        await refreshOutputs();
      } else if (current.status === "failed") {
        AshToast.show(`Export failed: ${current.error || "something went wrong"}`, { kind: "bad" });
        render();
      } else {
        showBurnProgress(current);
        burnPollTimer = setTimeout(() => pollBurn(burnJobId), POLL_BURN_MS);
      }
    }

    async function queueBurn() {
      if (!job) return;
      try {
        const res = await AshApi.request(api(jobId, "/burn"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preset: job.options.preset }),
        });
        if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't queue the export"));
        const burnJob = await res.json();
        showBurnProgress(burnJob);
        burnPollTimer = setTimeout(() => pollBurn(burnJob.id), POLL_BURN_MS);
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
      }
    }

    // A burn already queued for this job (from an earlier visit, or from
    // the Studio's own "Burn this look") shares this job's output_dir --
    // find it so "you can navigate away" holds across a reload too.
    async function findRunningBurn() {
      try {
        const all = await getJson("/api/jobs");
        return all.find((j) => j.id !== jobId && j.output_dir && j.output_dir === job.output_dir
          && (j.status === "pending" || j.status === "running")) || null;
      } catch (err) {
        return null;
      }
    }

    async function pollJob() {
      try {
        job = await getJson(api(jobId));
      } catch (err) {
        toggle.disabled = true;
        return;
      }
      if (job.status !== "done") {
        render();
        jobPollTimer = setTimeout(pollJob, POLL_JOB_MS);
        return;
      }
      await refreshOutputs();
      if (!pickVideo(outputs)) {
        const running = await findRunningBurn();
        if (running) {
          if (running.status === "done") await refreshOutputs();
          else { showBurnProgress(running); burnPollTimer = setTimeout(() => pollBurn(running.id), POLL_BURN_MS); }
        }
      }
    }

    pollJob();
    return { destroy: clearTimers };
  }

  function mount(jobId, container) {
    const target = container || document.getElementById("export");
    return mountOne(jobId, target);
  }

  window.AshExport = { mount };

  // Self-mount on the Studio page (/studio/{id}): the interfaces table's
  // `mount(jobId)` is the contract other pages call explicitly (the Styles
  // page); the Studio's own #export is this file's job to wire.
  //
  // Only on /studio/. The Styles page has an #export too, and taking the
  // last path segment there asked the server for a job called
  // "style-editor" -- a 404 in the console on every visit, and a burn
  // button that could never work.
  const studioMount = document.getElementById("export");
  const parts = location.pathname.split("/").filter(Boolean);
  if (studioMount && parts.length === 2 && parts[0] === "studio") {
    const jobId = decodeURIComponent(parts[1]);
    if (jobId) mount(jobId, studioMount);
  }
})();
