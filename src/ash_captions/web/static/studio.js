/* Studio page: play a finished job with its captions drawn live, click
   through looks (each click re-renders the .ass server-side and reloads the
   track in place), then burn the chosen look. No text editing, no timeline
   -- the team's ask is "pick a style we like", not "edit captions". */
(function () {
  "use strict";

  const jobId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const $ = (id) => document.getElementById(id);
  const els = {
    videoName: $("video-name"),
    styleName: $("style-name"),
    status: $("status-pill"),
    burnBtn: $("burn-btn"),
    stage: $("stage"),
    frame: $("frame"),
    video: $("video"),
    message: $("stage-message"),
    controls: $("controls"),
    transcript: $("transcript"),
    looksList: $("looks-list"),
    looksHint: $("looks-hint"),
    toast: $("toast"),
  };
  const POSITION_ORDER = ["top", "center", "bottom", "lower_third"];
  const POSITION_LABEL = { top: "Top", center: "Centre", bottom: "Bottom", lower_third: "Lower third" };
  const STATUS_LABEL = { pending: "waiting in the queue", running: "still being captioned", failed: "failed" };
  const api = (suffix) => `/api/jobs/${encodeURIComponent(jobId)}${suffix}`;
  const assUrl = () => `${api("/ass")}?v=${Date.now()}`; // bust the browser cache per restyle

  let job = null;
  let styles = [];
  let fonts = [];
  let player = null;
  let live = false; // live = original footage + JASSUB overlay; false = burned output
  let busy = false;
  let toastTimer = null;

  // ---- small UI helpers ----

  function setStatus(text, kind) {
    els.status.textContent = text;
    els.status.className = `status-pill${kind ? ` ${kind}` : ""}`;
  }

  function showToast(message, options) {
    const { href, linkText, ms } = options || {};
    els.toast.textContent = message;
    if (href) {
      const a = document.createElement("a");
      a.href = href;
      a.textContent = linkText || "Open";
      els.toast.appendChild(a);
    }
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, ms || 6000);
  }

  function stageMessage(title, body, withQueueLink) {
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

  // ---- title / fonts ----

  function renderTitle() {
    els.videoName.textContent = job.filename;
    document.title = `${job.filename} - Studio - ASH Captions`;
    els.styleName.textContent = "Look: ";
    const b = document.createElement("b");
    b.textContent = job.options.preset;
    els.styleName.appendChild(b);
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

  // ---- looks strip ----

  function glyph(layout) {
    const span = document.createElement("span");
    const position = POSITION_ORDER.includes(layout.position) ? layout.position : "bottom";
    const align = ["left", "center", "right"].includes(layout.align) ? layout.align : null;
    span.className = `look-glyph ${position}${align ? ` align-${align}` : ""}`;
    span.title = POSITION_LABEL[position] + (align ? `, ${align}` : "");
    span.appendChild(document.createElement("i"));
    return span;
  }

  function lookCard(style, enabled) {
    const d = style.definition || {};
    const colors = d.colors || {};
    const active = d.active_word || {};
    const card = document.createElement("button");
    card.type = "button";
    card.className = "look";
    card.dataset.name = style.name;
    card.disabled = !enabled;
    card.title = enabled ? `Preview "${style.name}"` : "Looks can't be previewed on the burned output";

    const sample = document.createElement("div");
    sample.className = "look-sample";
    sample.style.fontFamily = `${JSON.stringify(d.font || "Inter")}, sans-serif`;
    sample.style.fontSize = `${Math.round(Math.min(24, Math.max(15, (d.size || 72) / 3.8)))}px`;
    sample.style.color = colors.text || "#fff";
    sample.style.letterSpacing = `${(d.letter_spacing || 0) * 0.02}em`;
    sample.style.textTransform = d.uppercase ? "uppercase" : "none";
    sample.style.textShadow = `0 0 2px ${colors.outline || "#000"}, 0 2px 3px ${colors.shadow || "transparent"}`;
    const words = ["Pick", "this", "look"];
    words.forEach((word, i) => {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = word;
      if (i === 1) {
        w.style.color = colors.active || colors.text || "#fff";
        const boxed = active.box || active.effect === "box" || active.effect === "scale_box";
        if (boxed && colors.box) w.style.background = colors.box;
        if (active.effect === "glow") w.style.textShadow = `0 0 8px ${colors.active || "#fff"}`;
      }
      sample.appendChild(w);
    });

    const foot = document.createElement("div");
    foot.className = "look-foot";
    const name = document.createElement("span");
    name.className = "look-name";
    name.textContent = style.customized_locally ? `${style.name} (customized)` : style.name;
    foot.appendChild(name);
    foot.appendChild(glyph(d.layout || {}));

    card.appendChild(sample);
    card.appendChild(foot);
    card.addEventListener("click", () => applyLook(style.name));
    return card;
  }

  function renderLooks(enabled) {
    els.looksList.innerHTML = "";
    const groups = new Map(POSITION_ORDER.map((p) => [p, []]));
    for (const style of styles) {
      const position = ((style.definition || {}).layout || {}).position;
      (groups.get(position) || groups.get("bottom")).push(style);
    }
    for (const [position, list] of groups) {
      if (list.length === 0) continue;
      const group = document.createElement("section");
      group.className = "look-group";
      const h3 = document.createElement("h3");
      h3.textContent = `${POSITION_LABEL[position]} · ${list.length}`;
      group.appendChild(h3);
      for (const style of list) group.appendChild(lookCard(style, enabled));
      els.looksList.appendChild(group);
    }
    if (!enabled) {
      els.looksHint.textContent =
        "The original footage is gone, so this is the burned result and looks can't be changed.";
    }
    highlightCurrent();
  }

  function highlightCurrent() {
    for (const card of els.looksList.querySelectorAll(".look")) {
      card.classList.toggle("current", Boolean(job) && card.dataset.name === job.options.preset);
    }
    const current = els.looksList.querySelector(".look.current");
    if (current) current.scrollIntoView({ block: "nearest" });
  }

  async function applyLook(name) {
    if (busy || !job || name === job.options.preset) return;
    busy = true;
    els.looksList.classList.add("busy");
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
      highlightCurrent();
      setStatus("Ready", "ok");
    } catch (err) {
      setStatus("Error", "bad");
      showToast(err.message, { ms: 8000 });
    } finally {
      busy = false;
      els.looksList.classList.remove("busy");
    }
  }

  // ---- burn ----

  async function burn() {
    if (!job || els.burnBtn.disabled) return;
    els.burnBtn.disabled = true;
    setStatus("Queueing burn…", "busy");
    try {
      const res = await AshApi.request(api("/burn"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset: job.options.preset }),
      });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't queue the burn"));
      setStatus("Burn queued", "ok");
      showToast("Burning… you can watch it in the queue.", { href: "/", linkText: "Open the queue", ms: 12000 });
      await refreshBurnState();
    } catch (err) {
      setStatus("Error", "bad");
      showToast(err.message, { ms: 8000 });
      els.burnBtn.disabled = false;
    }
  }

  // Disabled while a burn of this same footage is already waiting or
  // running -- queueing a second one would just waste the GPU.
  async function refreshBurnState() {
    if (!job || !live) return;
    let jobs;
    try {
      jobs = await loadJson("/api/jobs", "the queue");
    } catch (err) {
      return;
    }
    const sameInput = (j) => (job.input_path ? j.input_path === job.input_path : j.filename === job.filename);
    const inFlight = jobs.find(
      (j) => j.id !== job.id && (j.status === "pending" || j.status === "running") && j.options && j.options.burn_in && sameInput(j)
    );
    els.burnBtn.disabled = Boolean(inFlight);
    els.burnBtn.title = inFlight
      ? `A burn of this video (${inFlight.options.preset}) is already ${inFlight.status === "running" ? "running" : "waiting"} in the queue.`
      : "Queue a burn of this video in the current look";
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
        chips[idx].scrollIntoView({ inline: "center", block: "nearest" });
      }
    });
  }

  // ---- page states ----

  function notFound() {
    stageMessage("Job not found", "There is no job with this id. It may have been removed from the queue.", true);
    setStatus("Not found", "bad");
    els.videoName.textContent = "Unknown job";
  }

  function waitUntilDone() {
    const say = () => {
      stageMessage(
        "Not finished yet",
        `This job is ${STATUS_LABEL[job.status] || job.status}. Studio opens once the captions are written -- this page updates by itself.`,
        true
      );
      setStatus(job.status === "failed" ? "Failed" : "Waiting for the job", job.status === "failed" ? "bad" : "busy");
    };
    say();
    if (job.status === "failed") {
      stageMessage("This job failed", job.error || "Something went wrong while captioning it.", true);
      return;
    }
    const timer = setInterval(async () => {
      let latest;
      try {
        latest = await fetchJob();
      } catch (err) {
        return;
      }
      if (!latest) return;
      job = latest;
      if (job.status === "done") {
        clearInterval(timer);
        location.reload();
      } else if (job.status === "failed") {
        clearInterval(timer);
        say();
        stageMessage("This job failed", job.error || "Something went wrong while captioning it.", true);
      }
    }, 3000);
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
      stageMessage("Couldn't reach ASH Captions", `${err.message}. Is the terminal window still open?`, true);
      return;
    }
    if (!found) return notFound();
    job = found;
    renderTitle();
    if (job.status !== "done") return waitUntilDone();

    try {
      [styles, fonts] = await Promise.all([
        loadJson("/api/styles", "the caption styles"),
        loadJson("/api/fonts/files", "the bundled fonts"),
      ]);
    } catch (err) {
      showToast(err.message, { ms: 8000 });
    }
    installFontFaces(fonts);

    const source = await pickSource();
    if (!source) {
      stageMessage(
        "Video not available",
        "The original footage has been moved or deleted and this job wasn't burned in, so there is nothing to play. The caption files are still in the job's output folder.",
        true
      );
      setStatus("No video", "bad");
      renderLooks(false);
      return;
    }
    live = source.live;
    player = AshStudioPlayer.createPlayer({
      stage: els.stage,
      frame: els.frame,
      video: els.video,
      playBtn: $("play-btn"),
      muteBtn: $("mute-btn"),
      seek: $("seek"),
      timeLabel: $("time-label"),
    });
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
      els.burnBtn.title = "Queue a burn of this video in the current look";
    } else {
      setStatus("Burned result", "ok");
      els.burnBtn.title = "The original footage is gone, so there is nothing to burn from.";
      showToast("The original footage is gone, so this is the burned result. Looks can't be previewed here.", { ms: 12000 });
    }
    renderLooks(live);
    loadTranscript();
    refreshBurnState();
    setInterval(refreshBurnState, 5000);
  }

  els.burnBtn.addEventListener("click", burn);
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || !player) return;
    const target = e.target || document.body;
    const tag = target.tagName || "";
    // Form fields, links and the transport buttons handle Space themselves
    // (Space on the play button already toggles). Anywhere else -- the
    // page, a look card, a transcript chip -- Space is play/pause.
    if (["INPUT", "SELECT", "TEXTAREA", "A"].includes(tag) || target.classList.contains("ctl")) return;
    e.preventDefault();
    player.toggle();
  });

  boot();
})();
