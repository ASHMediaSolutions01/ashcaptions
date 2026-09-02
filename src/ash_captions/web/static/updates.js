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

  async function checkForUpdate(onError) {
    let info;
    try {
      const res = await AshApi.request("/api/update");
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't check for updates"));
      info = await res.json();
    } catch (err) {
      // A failed check is worth a quiet note, never a broken page.
      if (onError) onError(`Couldn't check for updates: ${err.message}`);
      return;
    }
    if (!info) return;

    updateBannerDetail.textContent =
      `Version ${info.version} (${formatMegabytes(info.size_bytes)})` + (info.notes ? ` -- ${info.notes}` : "");
    if (info.blocked_reason) queueBusyReason = info.blocked_reason;
    updateBanner.hidden = false;
    updateButtonState();
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
