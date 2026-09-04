/* Sets the Studio <video>'s poster to the job's thumbnail -- the same
   320-px JPEG the queue card shows (GET /api/jobs/{id}/thumb) -- so a
   clip whose first frame is white or black does not look broken while
   it loads. The page is one static file for every job, so the id comes
   from the URL exactly as studio.js reads it. A job with no thumb 404s
   and the browser simply shows the first frame. */
(function () {
  "use strict";

  const jobId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const video = document.getElementById("video");
  if (!jobId || !video) return;
  video.poster = `/api/jobs/${encodeURIComponent(jobId)}/thumb`;
})();
