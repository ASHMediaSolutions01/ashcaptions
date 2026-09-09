/* In-app updates (spec 11.4): the banner and the apply/poll/reload flow.
   Split out of app.js so that file stays about jobs. app.js tells this
   module whether the queue is busy (AshUpdates.setQueueBusy) so the
   button disables itself the moment a job starts running.

   The click on "Update now" IS the consent -- deliberately no confirmation
   dialog here (a second "are you sure?" just trains people to click
   through unread). Applying restarts the app, which is why the button
   says so right next to itself rather than behind a dialog. The server
   only ever reports an update on an installed build (never a source
   checkout), so this banner simply never appears there. */
(function () {
  "use strict";

  const updateBanner = document.getElementById("update-banner");
  const updateBannerDetail = document.getElementById("update-banner-detail");
  const updateBannerReason = document.getElementById("update-banner-reason");
  const updateNowBtn = document.getElementById("update-now-btn");
  const statusLine = document.getElementById("update-status");
  const checkBtn = document.getElementById("update-check-btn");

  let queueBusyReason = null; // set from the live job list; overrides the server's snapshot reason

  function formatMegabytes(bytes) {
    return `${Math.round(bytes / 1024 / 1024)} MB`;
  }

  function updateButtonState() {
    if (updateBanner.hidden) return;
    updateNowBtn.disabled = !!queueBusyReason;
    updateBannerReason.hidden = !queueBusyReason;
    updateBannerReason.textContent = queueBusyReason || "";
  }

  function setQueueBusy(reason) {
    queueBusyReason = reason || null;
    updateButtonState();
  }

  // The one line at the foot of the page. It is always saying something,
  // which is the whole point: before this, an updater that was working and
  // one that was dead both showed nothing at all.
  function renderStatus(status) {
    if (!statusLine) return;
    const name = `ASH Captions ${status.current_version}`;
    if (status.state === "available") {
      statusLine.textContent = `${name} — version ${status.version} is available`;
    } else if (status.state === "up_to_date") {
      statusLine.textContent = `${name} — up to date`;
    } else {
      statusLine.textContent = `${name} — ${status.detail || "update status unknown"}`;
    }
    // Nothing to re-check on a copy that can never update itself.
    if (checkBtn) checkBtn.hidden = status.state === "unavailable";
  }

  function renderBanner(status) {
    if (status.state !== "available") {
      updateBanner.hidden = true;
      return;
    }
    updateBannerDetail.textContent =
      `Version ${status.version} (${formatMegabytes(status.size_bytes)})` +
      (status.notes ? ` -- ${status.notes}` : "");
    if (status.blocked_reason) queueBusyReason = status.blocked_reason;
    updateBanner.hidden = false;
    updateButtonState();
  }

  async function fetchStatus(path, options) {
    const res = await AshApi.request(path, options);
    if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't check for updates"));
    return res.json();
  }

  async function checkForUpdate(onError) {
    let status;
    try {
      status = await fetchStatus("/api/update/status");
    } catch (err) {
      // A failed check is worth a quiet note, never a broken page.
      if (statusLine) statusLine.textContent = `Couldn't check for updates: ${err.message}`;
      if (onError) onError(`Couldn't check for updates: ${err.message}`);
      return;
    }
    renderStatus(status);
    renderBanner(status);
  }

  if (checkBtn) {
    checkBtn.addEventListener("click", async () => {
      checkBtn.disabled = true;
      if (statusLine) statusLine.textContent = "Checking for updates…";
      try {
        const status = await fetchStatus("/api/update/check", { method: "POST" });
        renderStatus(status);
        renderBanner(status);
      } catch (err) {
        if (statusLine) statusLine.textContent = `Couldn't check for updates: ${err.message}`;
      } finally {
        checkBtn.disabled = false;
      }
    });
  }

  updateNowBtn.addEventListener("click", async () => {
    updateNowBtn.disabled = true;
    updateBannerReason.hidden = false;
    updateBannerReason.textContent = "Starting the update…";

    try {
      const res = await AshApi.request("/api/update/apply", { method: "POST" });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Could not start the update"));
      const job = await res.json();
      pollUpdateApply(job.id);
    } catch (err) {
      updateBannerReason.textContent = err.message;
      updateNowBtn.disabled = !!queueBusyReason;
    }
  });

  const UPDATE_PHASE_LABEL = {
    pending: "Starting the update…",
    downloading: "Downloading the update…",
    applying: "Applying the update…",
  };

  function pollUpdateApply(jobId) {
    const timer = setInterval(async () => {
      let job;
      try {
        const res = await AshApi.request(`/api/update/apply/${encodeURIComponent(jobId)}`);
        if (!res.ok) throw new Error("Lost track of the update.");
        job = await res.json();
      } catch (err) {
        clearInterval(timer);
        updateBannerReason.textContent = err.message;
        updateNowBtn.disabled = !!queueBusyReason;
        return;
      }

      if (job.status === "done") {
        clearInterval(timer);
        updateBannerReason.textContent = "Update applied -- the app is restarting…";
        waitForRestartThenReload();
      } else if (job.status === "failed") {
        clearInterval(timer);
        updateBannerReason.textContent = job.error || "The update failed.";
        updateNowBtn.disabled = !!queueBusyReason;
      } else {
        updateBannerReason.textContent = UPDATE_PHASE_LABEL[job.status] || "Working…";
      }
    }, 1000);
  }

  function waitForRestartThenReload() {
    // The app process is about to exit and relaunch (spec 11.4) -- this
    // page's own connection will drop. Poll for it to come back rather
    // than making the editor remember to refresh manually.
    const timer = setInterval(async () => {
      try {
        const res = await fetch("/api/jobs", { cache: "no-store" });
        if (res.ok) {
          clearInterval(timer);
          window.location.reload();
        }
      } catch (err) {
        // still restarting; keep waiting
      }
    }, 2000);
  }

  window.AshUpdates = { checkForUpdate, setQueueBusy };
})();
