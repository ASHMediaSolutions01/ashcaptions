/* The queue: job cards (thumb, name, client, stage, clock, actions) kept
   up to date in place from each server snapshot -- so an inline "remove?"
   confirmation survives the next progress tick -- plus the housekeeping
   actions (Remove from list, Clear finished, Open folder, Copy path) and
   the finished-job toast. Loaded before app.js, which feeds it snapshots
   through AshQueue.render(jobs). */
(function () {
  "use strict";

  const jobList = document.getElementById("job-list");
  const emptyQueue = document.getElementById("empty-queue");
  const clearBtn = document.getElementById("clear-finished-btn");
  const clearConfirm = document.getElementById("clear-confirm");

  const STATUS_LABEL = { pending: "Waiting", running: "Working", done: "Done", failed: "Failed" };
  const STAGE_LABEL = {
    extract: "Extracting audio",
    transcribe: "Transcribing",
    translate: "Translating to English",
    postprocess: "Cleaning up the text",
    write: "Writing captions",
    cards_and_write: "Writing captions",
    matte: "Finding the speaker",
    burn: "Burning captions in",
  };

  const cards = new Map(); // job id -> { el, refs, status }
  const thumbFailed = new Set(); // ids whose thumb 404'd; don't ask again this visit
  let latest = [];

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

  function metaFor(job) {
    const o = job.options || {};
    const bits = [o.dialect || o.language, o.preset, o.burn_in ? "burn-in" : null, o.translate_to_english ? "+ English" : null, o.behind_speaker ? "behind speaker" : null];
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

    const thumb = el("img", "job-thumb");
    thumb.alt = "";
    thumb.width = 128;
    thumb.height = 72;
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
    root.appendChild(thumb);
    root.appendChild(missing);

    const body = el("div", "job-body");
    const top = el("div", "job-top");
    const name = el("span", "job-name");
    const badge = el("span", "badge");
    top.append(name, badge);
    const meta = el("div", "job-meta");
    const track = el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    const fill = el("div", "progress-fill");
    track.appendChild(fill);
    const statusLine = el("div", "job-status-line");
    const stage = el("span", "job-stage");
    const elapsed = el("span", "job-elapsed");
    statusLine.append(stage, elapsed);
    const error = el("div", "job-error");
    error.hidden = true;
    const actions = el("div", "job-actions");
    const confirm = el("div", "job-confirm");
    confirm.hidden = true;
    body.append(top, meta, track, statusLine, error, actions, confirm);
    root.appendChild(body);

    const refs = { thumb, missing, name, badge, meta, track, fill, stage, elapsed, error, actions, confirm };
    return { el: root, refs, status: null };
  }

  function updateCard(card, job) {
    const { refs } = card;
    const pct = job.status === "done" ? 100 : Math.round((job.progress || 0) * 100);
    card.el.className = `job ${job.status}`;
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

    refs.error.hidden = job.status !== "failed";
    if (job.status === "failed") refs.error.textContent = job.error || "Something went wrong.";

    // The thumb is asked for once; a job that had none gets one more try
    // when it finishes, since the burned output can stand in for a
    // source that has gone.
    const thumbUrl = `/api/jobs/${encodeURIComponent(job.id)}/thumb`;
    const retryOnDone = card.status !== null && card.status !== job.status && job.status === "done" && thumbFailed.has(job.id);
    if (card.status === null && !thumbFailed.has(job.id)) {
      refs.thumb.src = thumbUrl;
    } else if (card.status === null) {
      refs.thumb.hidden = true;
      refs.missing.hidden = false;
    } else if (retryOnDone) {
      thumbFailed.delete(job.id);
      refs.thumb.hidden = false;
      refs.missing.hidden = true;
      refs.thumb.src = `${thumbUrl}?v=${Date.now()}`;
    }

    if (card.status !== job.status) {
      refs.actions.innerHTML = "";
      refs.confirm.hidden = true;
      for (const action of actionsFor(job, card)) refs.actions.appendChild(action);
      card.status = job.status;
    }
  }

  // One primary action per row -- Open in Studio on a finished job, Retry
  // on a failed one -- and the housekeeping as subtle buttons after it.
  function actionsFor(job, card) {
    const out = [];
    if (job.status === "done") {
      const studio = el("a", "btn small primary", "Open in Studio");
      studio.href = `/studio/${encodeURIComponent(job.id)}`;
      out.push(studio);
    }
    if (job.status === "failed") out.push(button("Retry", "primary", (e) => retry(job, e.currentTarget)));
    if (job.status === "done" || job.status === "failed") {
      if (job.output_dir) {
        out.push(button("Open folder", "subtle", (e) => reveal(job, e.currentTarget)));
        out.push(button("Copy path", "subtle", (e) => copyPath(job, e.currentTarget)));
      }
      out.push(button("Remove", "quiet", () => askRemove(job, card)));
    }
    return out;
  }

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
    box.appendChild(button("Remove", "danger", () => removeJobs([job.id], box)));
    box.appendChild(button("Keep", "", () => { box.hidden = true; }));
    box.hidden = false;
    box.querySelector("button").focus();
  }

  async function removeJobs(ids, confirmBox) {
    for (const b of confirmBox.querySelectorAll("button")) b.disabled = true;
    const failures = [];
    for (const id of ids) {
      try {
        const res = await AshApi.request(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (!res.ok && res.status !== 404) throw new Error(await AshApi.errorDetail(res, "Couldn't remove the job"));
      } catch (err) {
        failures.push(err.message);
      }
    }
    confirmBox.hidden = true;
    if (failures.length) AshToast.show(failures[0], { kind: "bad" });
    if (window.AshApp) AshApp.refreshJobs();
  }

  function askClearFinished() {
    const finished = latest.filter((j) => j.status === "done" || j.status === "failed");
    if (finished.length === 0) return;
    clearConfirm.innerHTML = "";
    clearConfirm.appendChild(el("span", "", `Remove ${finished.length} finished job${finished.length === 1 ? "" : "s"} from the list? Their files stay.`));
    clearConfirm.appendChild(button("Clear finished", "danger", () => removeJobs(finished.map((j) => j.id), clearConfirm)));
    clearConfirm.appendChild(button("Keep", "", () => { clearConfirm.hidden = true; }));
    clearConfirm.hidden = false;
    clearConfirm.querySelector("button").focus();
  }
  clearBtn.addEventListener("click", askClearFinished);

  // ---- rendering ----

  function render(jobs) {
    latest = jobs || [];
    emptyQueue.hidden = latest.length > 0;
    clearBtn.hidden = !latest.some((j) => j.status === "done" || j.status === "failed");
    if (clearBtn.hidden) clearConfirm.hidden = true;
    const seen = new Set();
    let cursor = jobList.firstElementChild;
    for (const job of latest) {
      seen.add(job.id);
      let card = cards.get(job.id);
      if (!card) { card = buildCard(job); cards.set(job.id, card); }
      updateCard(card, job);
      if (card.el !== cursor) jobList.insertBefore(card.el, cursor);
      else cursor = cursor.nextElementSibling;
    }
    for (const [id, card] of cards) {
      if (!seen.has(id)) { card.el.remove(); cards.delete(id); }
    }
    tickClocks();
  }

  function tickClocks() {
    const now = Date.now();
    for (const node of jobList.querySelectorAll(".job-elapsed[data-since]")) {
      node.textContent = `${node.dataset.label} ${formatDuration(now - Number(node.dataset.since))}`;
    }
  }
  setInterval(tickClocks, 1000);

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
      AshToast.show(`${job.filename} failed: ${job.error || "something went wrong"}`, { kind: "bad", ms: 0 });
      AshNotify.notify("Captioning failed", `${job.filename}: ${job.error || "something went wrong"}`);
    }
  }

  window.AshQueue = { render, jobFinished, formatDuration };
})();
