/* The per-burn choices on the Studio's top bar -- 9:16 reel and captions
   behind the speaker -- read at burn time and never stored: the same
   interview is cut for a reel and for YouTube from one transcript, and a
   burn is the moment the editor can see the footage the choice applies
   to. Loaded before studio.js, which mounts this once the job is known
   and asks for the flags when it queues a burn. */
(function () {
  "use strict";

  const reframeBtn = document.getElementById("reframe-btn");
  const behindBtn = document.getElementById("behind-btn");

  function on(btn) { return !!btn && !btn.hidden && btn.getAttribute("aria-pressed") === "true"; }
  function toggle(btn) { btn.setAttribute("aria-pressed", on(btn) ? "false" : "true"); }

  // Offered only for landscape footage: a vertical source has nothing to
  // crop, and offering the choice there is noise.
  function offerReframe(video) {
    if (!reframeBtn || !video || !video.videoWidth || !video.videoHeight) return;
    reframeBtn.hidden = video.videoWidth <= video.videoHeight;
  }

  function mount({ job, live, video }) {
    if (reframeBtn) {
      reframeBtn.addEventListener("click", () => toggle(reframeBtn));
      if (video) video.addEventListener("loadedmetadata", () => offerReframe(video));
      offerReframe(video);
    }
    if (behindBtn) {
      // The person is cut out of the original footage, so this needs the
      // source on hand; it starts the way the job was submitted.
      behindBtn.hidden = !live;
      const asked = !!(job && job.options && job.options.behind_speaker);
      behindBtn.setAttribute("aria-pressed", asked ? "true" : "false");
      behindBtn.addEventListener("click", () => toggle(behindBtn));
    }
  }

  function hide() {
    for (const btn of [reframeBtn, behindBtn]) if (btn) btn.hidden = true;
  }

  function flags() {
    return { reframe: on(reframeBtn), behind_speaker: on(behindBtn) };
  }

  window.AshBurnFlags = { mount, hide, flags };
})();
