/* The job card (frame, name, client, language, state, when, actions),
   kept up to date in place from each server snapshot -- so an inline
   "remove?" confirmation survives the next progress tick -- plus its
   actions (Open in Studio, Retry, Export, and a More menu with Open
   folder, Copy path and Remove) and the finished-job toast. The frame
   leads: it takes the footage's own shape, and a burn shows its burned
   frame. Which list a card sits in is decided by queue_stream.js, which
   calls AshQueue.renderInto(container, jobs, { quiet }) -- quiet cards
   (Earlier) keep the accent off their Open in Studio. */
(function () {
  "use strict";

  const STATUS_LABEL = { pending: "Waiting", running: "Working", done: "Done", failed: "Failed" };
  const STAGE_LABEL = {
    extract: "Extracting audio",
    transcribe: "Transcribing",
    translate: "Translating to English",
    postprocess: "Cleaning up the text",
    write: "Writing captions",
    cards_and_write: "Writing captions",
    matte: "Finding the speaker",
    reframe: "Framing the reel",
    burn: "Burning captions in",
  };
  const NEW_CARD_MS = 15000; // a job this young enters with the one authored motion

  const cards = new Map(); // job id -> { el, refs, status }
  const thumbFailed = new Set(); // ids whose thumb 404'd; don't ask again this visit

  // ---- formatting ----

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
    return `${Math.floor(m / 60)} h ${m % 60} min`;
  }

  function parseTime(iso) {
    const t = iso ? Date.parse(iso) : NaN;
    return Number.isNaN(t) ? null : t;
  }

  // "12 min ago" for today, the weekday and time for the past week, the
  // date after that: enough to tell this morning's reel from last month's.
  function formatWhen(t) {
    const age = Date.now() - t;
    if (age < 60000) return "just now";
    if (age < 3600000) return `${Math.floor(age / 60000)} min ago`;
    const d = new Date(t);
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (age < 86400000 && d.getDate() === new Date().getDate()) return `today ${time}`;
    if (age < 7 * 86400000) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }

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

  // What this job was asked for. A transcribe job names its language and
  // the speaker-names choice; a burn from the Studio names the look and
  // the shape it was burned in, because that is the file it produced.
  function metaFor(job) {
    const o = job.options || {};
    const code = o.dialect || o.language;
    const bits = [window.AshSubmit ? AshSubmit.labelFor(code) : code];
    if (o.burn_in) bits.push(`${o.preset} burned in`, o.reframe ? "9:16 reel" : null, o.behind_speaker ? "behind speaker" : null);
    bits.push(o.translate_to_english ? "+ English" : null, o.speaker_labels ? "speaker names" : null);
    return bits.filter(Boolean).join(" · ");
  }

  // ---- card construction ----

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function button(label, className, onClick) {
    const b = el("button", `btn small${className ? ` ${className}` : ""}`, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function buildCard(job) {
    const root = el("article", "job");
    root.dataset.id = job.id;
    root.setAttribute("aria-label", job.filename || "Job");
    const created = parseTime(job.created_at);
    if (created && Date.now() - created < NEW_CARD_MS) {
      root.classList.add("is-new");
      root.addEventListener("animationend", () => root.classList.remove("is-new"), { once: true });
    }

    const frame = el("div", "job-frame");
    const thumb = el("img", "job-thumb");
    thumb.alt = "";
    thumb.loading = "lazy";
    const missing = el("div", "job-thumb missing", "No preview");
    missing.hidden = true;
    let retried = false;
    thumb.addEventListener("error", () => {
      // A job that has only just been queued can answer 404 for a moment
      // while its folder is being set up; one retry covers that.
      if (!retried && thumb.src) {
        retried = true;
        setTimeout(() => { thumb.src = `${thumb.src.split("?")[0]}?r=${Date.now()}`; }, 3000);
        return;
      }
      thumbFailed.add(job.id);
      thumb.hidden = true;
      missing.hidden = false;
    });
    frame.append(thumb, missing);
    root.appendChild(frame);

    const body = el("div", "job-body");
    const top = el("div", "job-top");
    const name = el("span", "job-name");
    const badge = el("span", "badge");
    top.append(name, badge);
    const meta = el("div", "job-meta");
    const track = el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "Progress");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    const fill = el("div", "progress-fill");
    track.appendChild(fill);
    const statusLine = el("div", "job-status-line");
    const stage = el("span", "job-stage");
    const elapsed = el("span", "job-elapsed");
    const when = el("time", "job-when");
    statusLine.append(stage, elapsed, when);
    // A failure is one plain sentence first; the engine's text stays
    // available behind a disclosure, because it is what Ghazi needs and
    // not what the editor does.
    const error = el("div", "job-error");
    error.hidden = true;
    const reason = el("div", "job-reason");
    const details = document.createElement("details");
    details.className = "disclosure job-error-details";
    const summary = document.createElement("summary");
    summary.textContent = "Technical details";
    const technical = el("pre", "job-technical");
    details.append(summary, technical);
    error.append(reason, details);
    const actions = el("div", "job-actions");
    const confirm = el("div", "job-confirm");
    confirm.hidden = true;
    body.append(top, meta, track, statusLine, error, actions, confirm);
    root.appendChild(body);

    const refs = { thumb, missing, name, badge, meta, track, fill, stage, elapsed, when, error, reason, details, technical, actions, confirm };
    return { el: root, refs, status: null, quiet: false };
  }

  function updateCard(card, job, quiet) {
    const { refs } = card;
    const pct = job.status === "done" ? 100 : Math.round((job.progress || 0) * 100);
    card.el.className = `job ${job.status}${card.el.classList.contains("is-new") ? " is-new" : ""}${quiet ? " quiet" : ""}`;
    refs.name.textContent = job.filename;
    refs.badge.className = `badge ${job.status}`;
    refs.badge.textContent = STATUS_LABEL[job.status] || job.status;
    refs.meta.innerHTML = "";
    if (job.options && job.options.client) {
      refs.meta.appendChild(el("span", "client", job.options.client));
      refs.meta.appendChild(document.createTextNode(" · "));
    }
    refs.meta.appendChild(document.createTextNode(metaFor(job)));
    // A finished row shows its badge and "Finished in 2 min"; the bar and a
    // second "Done" only repeated what the badge says.
    const finished = job.status === "done" || job.status === "failed";
    refs.track.hidden = finished;
    refs.track.setAttribute("aria-valuenow", String(pct));
    refs.fill.style.width = `${pct}%`;
    refs.fill.className = `progress-fill${job.status === "running" ? " live" : ""}`;

    let stageText = "";
    if (job.status === "running") stageText = `${stageLabel(job)} · ${pct}%`;
    else if (job.status === "pending") stageText = "Waiting in the queue";
    refs.stage.textContent = stageText;
    const [label, since] = elapsedFor(job);
    refs.elapsed.textContent = label;
    if (since) { refs.elapsed.dataset.since = String(since); refs.elapsed.dataset.label = label; }
    else { delete refs.elapsed.dataset.since; delete refs.elapsed.dataset.label; }
    const at = finished ? parseTime(job.updated_at) : null;
    refs.when.hidden = !at;
    if (at) {
      refs.when.dateTime = new Date(at).toISOString();
      refs.when.textContent = formatWhen(at);
      refs.when.title = new Date(at).toLocaleString();
    }

    refs.error.hidden = job.status !== "failed";
    if (job.status === "failed") {
      refs.reason.textContent = job.reason || job.error || "Something went wrong.";
      const technical = job.error || "";
      refs.technical.textContent = technical;
      // Nothing to disclose when the reason already is the whole text.
      refs.details.hidden = !technical || technical === refs.reason.textContent;
      if (!refs.details.hidden) refs.details.open = false;
    }

    // The thumb is asked for once; a job that had none gets one more try
    // when it finishes, since the burned output can stand in for a
    // source that has gone.
    const thumbUrl = `/api/jobs/${encodeURIComponent(job.id)}/thumb`;
    const becameDone = card.status !== null && card.status !== job.status && job.status === "done";
    if (card.status === null && !thumbFailed.has(job.id)) {
      refs.thumb.src = thumbUrl;
    } else if (card.status === null) {
      refs.thumb.hidden = true;
      refs.missing.hidden = false;
    } else if (becameDone && thumbFailed.has(job.id)) {
      thumbFailed.delete(job.id);
      refs.thumb.hidden = false;
      refs.missing.hidden = true;
      refs.thumb.src = `${thumbUrl}?v=${Date.now()}`;
    } else if (becameDone && job.options && job.options.burn_in) {
      // The burn has a frame of its own now: the captions on the footage.
      refs.thumb.src = `${thumbUrl}?v=${Date.now()}`;
    }

    if (card.status !== job.status || card.quiet !== quiet) {
      refs.actions.innerHTML = "";
      refs.confirm.hidden = true;
      for (const action of actionsFor(job, card, quiet)) refs.actions.appendChild(action);
      card.status = job.status;
      card.quiet = quiet;
    }
  }

  // One primary action per row -- Open in Studio on a finished job, Retry
  // on a failed one -- then Export, then More for the housekeeping. On a
  // quiet (Earlier) card the accent comes off, so the fill still means
  // "the next thing" rather than "every card".
  function actionsFor(job, card, quiet) {
    const out = [];
    if (job.status === "done") {
      const studio = el("a", `btn small${quiet ? " subtle" : " primary"}`, "Open in Studio");
      studio.href = `/studio/${encodeURIComponent(job.id)}`;
      out.push(studio);
    }
    if (job.status === "failed") {
      // Retry leads only when it can change something. A file that is not
      // a video fails the same way every time, and a primary Retry there
      // is how an editor ends up retrying three times and escalating.
      const canHelp = job.retryable !== false;
      const label = canHelp ? "Retry" : "Try again";
      out.push(button(label, canHelp ? "primary" : "subtle", (e) => retry(job, e.currentTarget)));
    }
    if (job.status === "done") {
      const exportSlot = el("span", "job-export");
      out.push(exportSlot);
      if (window.AshExport) AshExport.mount(job.id, exportSlot);
    }
    if (job.status === "done" || job.status === "failed") out.push(moreMenu(job, card));
    return out;
  }

  // Open folder, Copy path and Remove behind one button: the same three
  // actions, one Tab stop, and the card's own line stays about the job.
  function moreMenu(job, card) {
    const details = document.createElement("details");
    details.className = "card-more";
    const summary = document.createElement("summary");
    summary.className = "btn small subtle";
    summary.textContent = "More";
    summary.setAttribute("aria-label", `More actions for ${job.filename}`);
    const menu = el("div", "card-menu");
    if (job.output_dir) {
      menu.appendChild(button("Open folder", "quiet", (e) => { details.open = false; reveal(job, e.currentTarget); }));
      menu.appendChild(button("Copy path", "quiet", (e) => copyPath(job, e.currentTarget)));
    }
    menu.appendChild(button("Remove", "quiet", () => { details.open = false; askRemove(job, card); }));
    details.append(summary, menu);
    return details;
  }
  document.addEventListener("click", (e) => {
    for (const open of document.querySelectorAll("details.card-more[open]")) {
      if (!open.contains(e.target)) open.open = false;
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    for (const open of document.querySelectorAll("details.card-more[open]")) { open.open = false; open.querySelector("summary").focus(); }
  });

  // ---- actions ----

  async function reveal(job, btn) {
    btn.disabled = true;
    try {
      const res = await AshApi.request(`/api/jobs/${encodeURIComponent(job.id)}/reveal`, { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't open the folder"));
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    } finally {
      btn.disabled = false;
    }
  }

  async function copyPath(job, btn) {
    try {
      await navigator.clipboard.writeText(job.output_dir);
      flash(btn, "Copied");
    } catch (err) {
      AshToast.show(`Couldn't copy. The folder is ${job.output_dir}`, { kind: "bad", ms: 12000 });
    }
  }

  function flash(btn, text) {
    const original = btn.textContent;
    btn.textContent = text;
    btn.classList.add("done-flash");
    setTimeout(() => { btn.textContent = original; btn.classList.remove("done-flash"); }, 1400);
  }

  async function retry(job, btn) {
    btn.disabled = true;
    try {
      const res = await AshApi.request(`/api/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not retry this job"));
      if (window.AshApp) AshApp.refreshJobs();
    } catch (err) {
      btn.disabled = false;
      AshToast.show(err.message, { kind: "bad" });
    }
  }

  function askRemove(job, card) {
    const box = card.refs.confirm;
    box.innerHTML = "";
    box.appendChild(el("span", "", "Remove this job from the list? The files in its folder stay."));
    box.appendChild(button("Remove", "danger", () => removeJob(job.id, box)));
    box.appendChild(button("Keep", "", () => { box.hidden = true; }));
    box.hidden = false;
    box.querySelector("button").focus();
  }

  async function removeJob(id, confirmBox) {
    for (const b of confirmBox.querySelectorAll("button")) b.disabled = true;
    try {
      const res = await AshApi.request(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(await AshApi.errorDetail(res, "Couldn't remove the job"));
    } catch (err) {
      AshToast.show(err.message, { kind: "bad" });
    }
    confirmBox.hidden = true;
    if (window.AshApp) AshApp.refreshJobs();
  }

  // ---- rendering ----

  // Puts exactly `jobs`, in order, into `container`, reusing the card an
  // id already has (a card may move from the recent list to Earlier as it
  // ages; it is the same element). Cards no longer in any list are dropped
  // once every list has been rendered for this snapshot.
  const placed = new Set();

  function renderInto(container, jobs, opts) {
    const quiet = !!(opts && opts.quiet);
    let cursor = container.firstElementChild;
    for (const job of jobs || []) {
      placed.add(job.id);
      let card = cards.get(job.id);
      if (!card) { card = buildCard(job); cards.set(job.id, card); }
      updateCard(card, job, quiet);
      if (card.el !== cursor) container.insertBefore(card.el, cursor);
      else cursor = cursor.nextElementSibling;
    }
    // Whatever is left past the cursor in this container belongs elsewhere now.
    while (cursor) { const next = cursor.nextElementSibling; cursor.remove(); cursor = next; }
    tickClocks(container);
  }

  // Called by queue_stream.js once both lists are placed for a snapshot.
  function prune() {
    for (const [id, card] of cards) {
      if (!placed.has(id)) { card.el.remove(); cards.delete(id); }
    }
    placed.clear();
  }

  function tickClocks(root) {
    const now = Date.now();
    for (const node of (root || document).querySelectorAll(".job-elapsed[data-since]")) {
      node.textContent = `${node.dataset.label} ${formatDuration(now - Number(node.dataset.since))}`;
    }
  }
  setInterval(() => tickClocks(document), 1000);

  // ---- a job this tab started has finished (called by studio_hook.js) ----

  function jobFinished(job) {
    const studioUrl = `/studio/${encodeURIComponent(job.id)}`;
    if (job.status === "done") {
      AshToast.show(`${job.filename} is done.`, {
        kind: "ok",
        ms: 15000,
        actions: [
          { label: "Open in Studio", href: studioUrl },
          { label: "Open folder", onClick: () => reveal(job, { disabled: false }), keep: true },
        ],
      });
      AshNotify.notify("Captions ready", `${job.filename} is done. Click to pick a look.`, () => location.assign(studioUrl));
    } else {
      const why = job.reason || job.error || "something went wrong";
      AshToast.show(`${job.filename} failed: ${why}`, { kind: "bad", ms: 0 });
      AshNotify.notify("Captioning failed", `${job.filename}: ${why}`);
    }
  }

  window.AshQueue = { renderInto, prune, jobFinished, formatDuration, formatWhen, metaFor };
})();
