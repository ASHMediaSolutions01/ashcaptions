/* The control page's hand-off to Studio: remembers which jobs this tab
   started, tells queue.js when one of them finishes (toast + desktop
   notification), and -- when "Open Studio when a job finishes" is on
   (default, remembered in localStorage) -- opens Studio in this tab.
   Loaded before app.js, which calls in at the points that need it. */
(function () {
  "use strict";

  const SETTING_KEY = "ash.openStudioWhenDone";
  const submittedHere = new Set();
  // Jobs that went out as part of a batch: their finishing is announced,
  // never followed -- jumping to the Studio for the first of five would
  // pull the editor off the page while the other four are still running.
  const inBatch = new Set();
  const setting = document.getElementById("open-studio-check");

  function readSetting() {
    try {
      const value = localStorage.getItem(SETTING_KEY);
      return value === null ? true : value === "1";
    } catch (err) {
      return true;
    }
  }
  function writeSetting(on) {
    try { localStorage.setItem(SETTING_KEY, on ? "1" : "0"); } catch (err) { /* private mode */ }
  }
  if (setting) {
    setting.checked = readSetting();
    setting.addEventListener("change", () => writeSetting(setting.checked));
  }

  function studioUrl(jobId) {
    return `/studio/${encodeURIComponent(jobId)}`;
  }

  // Kept for callers that still decorate a card themselves; queue.js now
  // builds the "Open in Studio" action as part of every finished card.
  function decorate(cardEl, job) {
    if (job.status !== "done" || cardEl.querySelector('a[href^="/studio/"]')) return;
    const link = document.createElement("a");
    link.className = "btn small primary";
    link.href = studioUrl(job.id);
    link.textContent = "Open in Studio";
    cardEl.appendChild(link);
  }

  // Called by submit.js with each job a submit just created; `batch`
  // says it was one of several.
  function noteSubmitted(job, opts) {
    if (!job || !job.id) return;
    submittedHere.add(job.id);
    if (opts && opts.batch) inBatch.add(job.id);
  }

  // Called by app.js on every queue snapshot. Only jobs this tab started
  // count: another editor's watch-folder job finishing must never yank
  // this page away or shout about it.
  function onJobs(jobs) {
    if (submittedHere.size === 0) return;
    for (const job of jobs || []) {
      if (!submittedHere.has(job.id)) continue;
      if (job.status !== "done" && job.status !== "failed") continue;
      submittedHere.delete(job.id);
      if (window.AshQueue) AshQueue.jobFinished(job);
      const batched = inBatch.delete(job.id);
      if (job.status === "done" && !batched && setting && setting.checked) {
        window.location.assign(studioUrl(job.id));
        return;
      }
    }
  }

  window.AshStudio = { decorate, noteSubmitted, onJobs };
})();
