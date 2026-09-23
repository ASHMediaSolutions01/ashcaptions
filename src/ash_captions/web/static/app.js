/* Control page: page-level errors, the live queue feed (one EventSource
   for the life of the page), the worker health line, and boot. The
   drawer is submit.js; which list a card lands in is queue_stream.js;
   the cards themselves are queue.js; the Studio hand-off is
   studio_hook.js; the client field and glossary are clients.js. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const appError = $("app-error");
  const connectionBanner = $("connection-banner");
  const queueHealth = $("queue-health");
  const navStatus = $("nav-status");

  // ---- Page-level errors: said out loud at the top, never swallowed ----

  function showAppError(message) { appError.textContent = message; appError.hidden = false; }
  function clearAppError() { appError.hidden = true; appError.textContent = ""; }

  async function loadJson(url, what) {
    const res = await AshApi.request(url);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, `Couldn't load ${what}`));
    return res.json();
  }

  // ---- Queue feed ----

  function renderJobs(jobs) {
    AshUpdates.setQueueBusy(
      (jobs || []).some((j) => j.status === "running") ? "A caption job is still running. Try again when the queue is clear." : null
    );
    AshStream.onSnapshot(jobs);
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
    if (health.lastPollAt) {
      // Coarse on purpose: this is a live region, and a number that ticks
      // every second is a screen reader that never stops talking.
      const age = Math.max(0, Date.now() - health.lastPollAt);
      const coarse = age < 60000 ? Math.floor(age / 10000) * 10000 : age;
      parts.push(age < 10000 ? "checked just now" : `checked ${AshQueue.formatDuration(coarse)} ago`);
    }
    parts.push(health.live ? "live" : "not connected");
    const text = parts.join(" · ");
    if (queueHealth.textContent !== text) queueHealth.textContent = text;
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

  // ---- Boot ----

  window.AshApp = { refreshJobs, loadJson, showAppError };
  AshSubmit.init();
  refreshJobs();
  connectEvents();
  AshUpdates.checkForUpdate((message) => { queueHealth.title = message; });
})();
