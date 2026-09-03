/* The control page's link to Studio: an "Open in Studio" button on every
   finished job, and -- when the "Open Studio when a job finishes" setting is
   on (default, remembered in localStorage) -- opening Studio in this tab the
   moment a job submitted from this tab finishes. Loaded before app.js, which
   calls into window.AshStudio at the three points that need it. */
(function () {
  "use strict";

  const SETTING_KEY = "ash.openStudioWhenDone";
  const submittedHere = new Set();
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
    try {
      localStorage.setItem(SETTING_KEY, on ? "1" : "0");
    } catch (err) {
      // private mode / blocked storage: the checkbox still works for this visit
    }
  }
  if (setting) {
    setting.checked = readSetting();
    setting.addEventListener("change", () => writeSetting(setting.checked));
  }

  function studioUrl(jobId) {
    return `/studio/${encodeURIComponent(jobId)}`;
  }

  // Called by app.js for every rendered job card.
  function decorate(cardEl, job) {
    if (job.status !== "done") return;
    const actions = document.createElement("div");
    actions.className = "job-actions";
    const link = document.createElement("a");
    link.className = "btn secondary";
    link.href = studioUrl(job.id);
    link.textContent = "Open in Studio";
    actions.appendChild(link);
    cardEl.appendChild(actions);
  }

  // Called by app.js with the job a submit just created.
  function noteSubmitted(job) {
    if (job && job.id) submittedHere.add(job.id);
  }

  // Called by app.js on every queue snapshot. Only jobs this tab started
  // count: another editor's watch-folder job finishing must never yank
  // this page away.
  function onJobs(jobs) {
    if (submittedHere.size === 0) return;
    for (const job of jobs || []) {
      if (!submittedHere.has(job.id)) continue;
      if (job.status === "failed") submittedHere.delete(job.id);
      if (job.status === "done") {
        submittedHere.delete(job.id);
        if (setting && setting.checked) {
          window.location.assign(studioUrl(job.id));
          return;
        }
      }
    }
  }

  window.AshStudio = { decorate, noteSubmitted, onJobs };
})();
