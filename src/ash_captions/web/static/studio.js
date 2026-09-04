/* Studio page: play a finished job with its captions drawn live, click
   through looks (each click re-renders the .ass server-side and reloads the
   track in place), then burn the chosen look. No text editing, no timeline
   -- the team's ask is "pick a style we like", not "edit captions". The
   looks strip itself (cards, filter, keys, Compare) is studio_looks.js. */
(function () {
  "use strict";

  const jobId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const $ = (id) => document.getElementById(id);
  const els = {
    thumb: $("job-thumb"),
    videoName: $("video-name"),
    styleName: $("style-name"),
    status: $("status-pill"),
    burnBtn: $("burn-btn"),
    revealBtn: $("reveal-btn"),
    copyBtn: $("copy-btn"),
    stage: $("stage"),
    frame: $("frame"),
    video: $("video"),
    message: $("stage-message"),
    wait: $("stage-wait"),
    controls: $("controls"),
    transcript: $("transcript"),
  };
  const STAGE_LABEL = {
    extract: "Extracting audio", transcribe: "Transcribing", translate: "Translating to English",
    postprocess: "Cleaning up the text", write: "Writing captions", matte: "Finding the speaker", burn: "Burning captions in",
  };
  const api = (suffix) => `/api/jobs/${encodeURIComponent(jobId)}${suffix}`;
  const assUrl = () => `${api("/ass")}?v=${Date.now()}`; // bust the browser cache per restyle

  let job = null;
  let fonts = [];
  let player = null;
  let live = false; // live = original footage + JASSUB overlay; false = burned output
  let busy = false;
  let looks = null;
  let burnJobId = null; // the burn this page queued, watched until it finishes

  // ---- small UI helpers ----

  function setStatus(text, kind) {
    els.status.textContent = text;
    els.status.className = `status-pill${kind ? ` ${kind}` : ""}`;
  }

  function stageMessage(title, body, withQueueLink) {
    els.wait.hidden = true;
    els.message.innerHTML = "";
    const strong = document.createElement("strong");
    strong.textContent = title;
    els.message.appendChild(strong);
    els.message.appendChild(document.createTextNode(body));
    if (withQueueLink) {
      els.message.appendChild(document.createElement("br"));
      const a = document.createElement("a");
      a.href = "/";
      a.textContent = "Back to the queue";
      els.message.appendChild(a);
    }
    els.message.hidden = false;
  }

  async function loadJson(url, what) {
    const res = await AshApi.request(url);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, `Couldn't load ${what}`));
    return res.json();
  }

  // null when the job doesn't exist; throws on anything else.
  async function fetchJob() {
    const res = await AshApi.request(api(""));
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't load this job"));
    return res.json();
  }

  // Is this stream there? A one-byte Range GET (200 or 206) -- the routes
  // are GET-only, so HEAD would be a 405 even when the file exists.
  async function probe(url) {
    try {
      return (await fetch(url, { headers: { Range: "bytes=0-0" } })).ok;
    } catch (err) {
      return false;
    }
  }

  // ---- title / fonts / folder actions ----

  function renderTitle() {
    els.videoName.textContent = job.filename;
    document.title = `${job.filename} - Studio - ASH Captions`;
    els.styleName.textContent = "Look: ";
    const b = document.createElement("b");
    b.textContent = job.options.preset;
    els.styleName.appendChild(b);
    if (job.options.client) els.styleName.appendChild(document.createTextNode(` · Client: ${job.options.client}`));
    els.thumb.src = api("/thumb");
    els.thumb.hidden = false;
    els.thumb.addEventListener("error", () => { els.thumb.hidden = true; }, { once: true });
    const hasFolder = Boolean(job.output_dir);
    els.revealBtn.hidden = !hasFolder;
    els.copyBtn.hidden = !hasFolder;
  }

  async function revealFolder() {
    els.revealBtn.disabled = true;
    try {
      const res = await AshApi.request(api("/reveal"), { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't open the folder"));
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    } finally {
      els.revealBtn.disabled = false;
    }
  }

  async function copyPath() {
    try {
      await navigator.clipboard.writeText(job.output_dir);
      const label = els.copyBtn.textContent;
      els.copyBtn.textContent = "Copied";
      els.copyBtn.classList.add("done-flash");
      setTimeout(() => { els.copyBtn.textContent = label; els.copyBtn.classList.remove("done-flash"); }, 1400);
    } catch (err) {
      AshToast.show(`Couldn't copy. The folder is ${job.output_dir}`, { kind: "bad", ms: 12000 });
    }
  }

  // @font-face for every served bundled font, so the look cards' type
  // samples are set in the real faces, not a system stand-in.
  function installFontFaces(list) {
    const rules = list.map((font) => {
      const format = /\.woff2(\?|$)/i.test(font.url) ? "woff2" : /\.otf(\?|$)/i.test(font.url) ? "opentype" : "truetype";
      return `@font-face{font-family:${JSON.stringify(font.family)};src:url(${JSON.stringify(font.url)}) format("${format}");font-display:swap;}`;
    });
    const style = document.createElement("style");
    style.textContent = rules.join("\n");
    document.head.appendChild(style);
  }

  // ---- applying a look ----

  async function applyLook(name) {
    if (busy || !job) return false;
    busy = true;
    $("looks-list").classList.add("busy");
    setStatus("Applying…", "busy");
    try {
      const res = await AshApi.request(api("/restyle"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset: name }),
      });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't apply that look"));
      job = await res.json();
      player.setTrack(assUrl()); // video keeps playing; only the captions change
      renderTitle();
      (window.AshStudio && AshStudio.onRestyled || []).forEach((fn) => fn(job));
      setStatus("Ready", "ok");
      return true;
    } catch (err) {
      setStatus("Error", "bad");
      AshToast.show(err.message, { kind: "bad" });
      return false;
    } finally {
      busy = false;
      $("looks-list").classList.remove("busy");
    }
  }

  // ---- burn ----

  function setBurnState(state) {
    // idle | pending | running | done | blocked
    els.burnBtn.innerHTML = "";
    els.burnBtn.classList.remove("done-flash");
    if (state === "pending" || state === "running") {
      const spin = document.createElement("span");
      spin.className = "spinner";
      spin.setAttribute("aria-hidden", "true");
      els.burnBtn.append(spin, document.createTextNode(state === "pending" ? " Queued…" : " Burning…"));
      els.burnBtn.disabled = true;
    } else if (state === "done") {
      els.burnBtn.textContent = "✓ Burned";
      els.burnBtn.classList.add("done-flash");
      els.burnBtn.disabled = false;
    } else {
      els.burnBtn.textContent = "Burn this look";
      els.burnBtn.disabled = state === "blocked" || !live;
    }
  }

  async function burn() {
    if (!job || els.burnBtn.disabled) return;
    setBurnState("pending");
    setStatus("Queueing burn…", "busy");
    try {
      const res = await AshApi.request(api("/burn"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset: job.options.preset }),
      });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't queue the burn"));
      burnJobId = (await res.json()).id;
      setStatus("Burn queued", "ok");
      AshToast.show(`Burning ${job.options.preset} into ${job.filename}. Watch it in the queue.`, { actions: [{ label: "Open the queue", href: "/" }], ms: 10000 });
      await refreshBurnState();
    } catch (err) {
      setStatus("Error", "bad");
      AshToast.show(err.message, { kind: "bad" });
      setBurnState("idle");
    }
  }

  // Polls the queue: a spinner while a burn of this footage is waiting or
  // running, a tick once the one this page queued has finished.
  async function refreshBurnState() {
    if (!job || !live) return;
    let jobs;
    try {
      jobs = await loadJson("/api/jobs", "the queue");
    } catch (err) {
      return;
    }
    const sameInput = (j) => (job.input_path ? j.input_path === job.input_path : j.filename === job.filename);
    const inFlight = jobs.find((j) => j.id !== job.id && (j.status === "pending" || j.status === "running") && j.options && j.options.burn_in && sameInput(j));
    if (inFlight) {
      setBurnState(inFlight.status);
      els.burnBtn.title = `A burn of this video (${inFlight.options.preset}) is ${inFlight.status === "running" ? "running" : "waiting"} in the queue.`;
      return;
    }
    const mine = burnJobId && jobs.find((j) => j.id === burnJobId);
    if (mine && mine.status === "done") {
      burnJobId = null;
      setBurnState("done");
      AshToast.show(`${job.filename} is burned in ${mine.options.preset}.`, { kind: "ok", ms: 15000, actions: [{ label: "Open folder", onClick: revealFolder, keep: true }] });
      setTimeout(() => setBurnState("idle"), 4000);
      return;
    }
    if (mine && mine.status === "failed") {
      burnJobId = null;
      AshToast.show(`The burn failed: ${mine.error || "something went wrong"}`, { kind: "bad", ms: 0 });
    }
    if (els.burnBtn.textContent !== "✓ Burned") setBurnState("idle");
    els.burnBtn.title = "Queue a burn of this video in the current look";
  }

  // ---- transcript strip ----

  function srtTime(text) {
    const m = /(\d+):(\d+):(\d+)[,.](\d+)/.exec(text);
    return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000 : 0;
  }

  function parseSrt(text) {
    const cues = [];
    for (const block of text.replace(/\r/g, "").split(/\n\n+/)) {
      const lines = block.split("\n").filter((l) => l.trim());
      const timeIdx = lines.findIndex((l) => l.includes("-->"));
      if (timeIdx < 0) continue;
      const [start, end] = lines[timeIdx].split("-->");
      const body = lines.slice(timeIdx + 1).join(" ").trim();
      if (body) cues.push({ start: srtTime(start), end: srtTime(end), text: body });
    }
    return cues;
  }

  async function loadTranscript() {
    let text;
    try {
      const res = await AshApi.request(api("/srt"));
      if (!res.ok) return;
      text = await res.text();
    } catch (err) {
      return;
    }
    const cues = parseSrt(text);
    if (cues.length === 0) return;
    els.transcript.innerHTML = "";
    const chips = cues.map((cue) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = cue.text;
      chip.title = `${AshStudioPlayer.formatTime(cue.start)} – ${AshStudioPlayer.formatTime(cue.end)}`;
      chip.addEventListener("click", () => player.seek(cue.start));
      els.transcript.appendChild(chip);
      return chip;
    });
    els.transcript.hidden = false;
    let activeIdx = -1;
    player.onTime((t) => {
      const idx = cues.findIndex((c) => t >= c.start && t < c.end);
      if (idx === activeIdx) return;
      if (activeIdx >= 0) chips[activeIdx].classList.remove("active");
      activeIdx = idx;
      if (idx >= 0) {
        chips[idx].classList.add("active");
        keepChipVisible(chips[idx]);
      }
    });
  }

  // Horizontal auto-scroll of the strip only: scrollIntoView would also
  // scroll the page/stage, so the strip's own scrollLeft is set instead.
  function keepChipVisible(chip) {
    const strip = els.transcript;
    const left = chip.offsetLeft - strip.offsetLeft;
    const right = left + chip.offsetWidth;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const target = left < strip.scrollLeft + 40 || right > strip.scrollLeft + strip.clientWidth - 40
      ? Math.max(0, left - (strip.clientWidth - chip.offsetWidth) / 2)
      : null;
    if (target !== null) strip.scrollTo({ left: target, behavior: reduce ? "auto" : "smooth" });
  }

  // ---- page states ----

  function notFound() {
    stageMessage("Job not found", "There is no job with this id. It may have been removed from the queue.", true);
    setStatus("Not found", "bad");
    els.videoName.textContent = "Unknown job";
  }

  function renderWaiting() {
    const running = job.status === "running";
    const pct = Math.round((job.progress || 0) * 100);
    $("wait-title").textContent = running ? "Still captioning" : "Waiting in the queue";
    $("wait-stage").textContent = running ? `${STAGE_LABEL[job.stage] || job.stage || "Working"} · ${pct}%` : "Studio opens by itself once the captions are written.";
    const since = Date.parse(job.started_at || job.created_at);
    const secs = Math.max(0, Math.round((Date.now() - since) / 1000));
    const took = secs < 60 ? `${secs} s` : `${Math.floor(secs / 60)} min`;
    $("wait-elapsed").textContent = Number.isNaN(since) ? "" : `${running ? "Running" : "Waiting"} for ${took}`;
    $("wait-fill").style.width = `${running ? Math.max(pct, 2) : 0}%`;
    const thumb = $("wait-thumb");
    if (!thumb.src) {
      thumb.src = api("/thumb");
      thumb.addEventListener("error", () => { thumb.hidden = true; }, { once: true });
      thumb.hidden = false;
    }
    els.message.hidden = true;
    els.wait.hidden = false;
    setStatus(running ? "Captioning…" : "Waiting", "busy");
  }

  function waitUntilDone() {
    if (job.status === "failed") {
      stageMessage("This job failed", job.error || "Something went wrong while captioning it.", true);
      setStatus("Failed", "bad");
      return;
    }
    renderWaiting();
    const timer = setInterval(async () => {
      let latest;
      try { latest = await fetchJob(); } catch (err) { return; }
      if (!latest) return;
      job = latest;
      if (job.status === "done") { clearInterval(timer); location.reload(); }
      else if (job.status === "failed") { clearInterval(timer); waitUntilDone(); }
      else renderWaiting();
    }, 2000);
  }

  async function pickSource() {
    if (await probe(api("/video"))) return { url: api("/video"), live: true };
    if (await probe(api("/output"))) return { url: api("/output"), live: false };
    return null;
  }

  async function boot() {
    if (!jobId) return notFound();
    let found;
    try {
      found = await fetchJob();
    } catch (err) {
      setStatus("Error", "bad");
      stageMessage("Couldn't reach ASH Captions", `${err.message}. Is it still running?`, true);
      return;
    }
    if (!found) return notFound();
    job = found;
    renderTitle();
    if (window.AshNav) AshNav.rememberStudioJob(job.id);
    if (job.status !== "done") return waitUntilDone();

    let styles = [];
    try {
      [styles, fonts] = await Promise.all([loadJson("/api/styles", "the caption styles"), loadJson("/api/fonts/files", "the bundled fonts")]);
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    }
    installFontFaces(fonts);
    looks = AshStudioLooks.createLooks(
      { list: $("looks-list"), filter: $("looks-filter"), hint: $("looks-hint"), compareBtn: $("compare-btn") },
      applyLook
    );

    const source = await pickSource();
    if (!source) {
      stageMessage("Video not available", "The original footage has been moved or deleted and this job wasn't burned in, so there is nothing to play. The caption files are still in the job's output folder.", true);
      setStatus("No video", "bad");
      looks.setStyles(styles, false, job.options.preset);
      return;
    }
    live = source.live;
    player = AshStudioPlayer.createPlayer({
      stage: els.stage, frame: els.frame, video: els.video,
      playBtn: $("play-btn"), muteBtn: $("mute-btn"), seek: $("seek"), timeLabel: $("time-label"),
    });
    window.AshStudio = Object.assign(window.AshStudio || {}, { player });
    els.controls.hidden = false;
    try {
      await player.load(source.url, { assUrl: live ? assUrl() : null, fonts });
    } catch (err) {
      stageMessage("Couldn't play the video", err.message, true);
      setStatus("Error", "bad");
      return;
    }
    if (live) {
      setStatus("Ready", "ok");
      setBurnState("idle");
    } else {
      setStatus("Burned result", "ok");
      els.burnBtn.title = "The original footage is gone, so there is nothing to burn from.";
      AshToast.show("The original footage is gone, so this is the burned result. Looks can't be previewed here.", { ms: 12000 });
    }
    (window.AshStudio && AshStudio.onReady || []).forEach((fn) => fn({ player, live, api, assUrl, getJob: () => job, setJob: (next) => { job = next; renderTitle(); } }));
    looks.setStyles(styles, live, job.options.preset);
    if (window.AshStudioCheck) AshStudioCheck.mount({ jobId, job, player, live }); else loadTranscript();
    refreshBurnState();
    setInterval(refreshBurnState, 3000);
  }

  els.burnBtn.addEventListener("click", burn);
  els.revealBtn.addEventListener("click", revealFolder);
  els.copyBtn.addEventListener("click", copyPath);
  document.addEventListener("keydown", (e) => {
    const target = e.target || document.body;
    const tag = target.tagName || "";
    if (["INPUT", "SELECT", "TEXTAREA", "A"].includes(tag) || target.classList.contains("ctl")) return;
    if (e.code === "Space" && player) { e.preventDefault(); player.toggle(); return; }
    if (!looks || !live) return;
    // Arrow keys and Enter walk the looks from anywhere on the page.
    if (target.closest && target.closest(".looks-list")) return; // the list handles its own keys
    if ((e.key === "c" || e.key === "C") && !e.ctrlKey && !e.metaKey) { looks.compare(); return; }
    if (looks.handleKey(e)) looks.focus();
  });

  boot();
})();
