/* The shared top nav (Queue / Studio / Styles / Help). Every page carries
   the same markup; this marks the current page and points "Studio" at the
   last job opened there (remembered by studio.js), else the newest
   finished job from the queue, else it stays disabled. */
(function () {
  "use strict";

  const LAST_STUDIO_KEY = "ash.lastStudioJob";
  const nav = document.querySelector(".app-nav");
  if (!nav) return;

  const here = location.pathname;
  for (const link of nav.querySelectorAll("a.nav-link")) {
    const target = link.getAttribute("href");
    const active = target === "/" ? here === "/" : here.startsWith(target);
    if (active) link.setAttribute("aria-current", "page");
  }

  function readLast() {
    try { return localStorage.getItem(LAST_STUDIO_KEY) || ""; } catch (err) { return ""; }
  }
  function rememberStudioJob(jobId) {
    try { localStorage.setItem(LAST_STUDIO_KEY, jobId); } catch (err) { /* private mode */ }
    pointStudioAt(jobId);
  }
  function pointStudioAt(jobId) {
    const link = nav.querySelector('a.nav-link[data-nav="studio"]');
    if (!link) return;
    if (jobId) {
      link.href = `/studio/${encodeURIComponent(jobId)}`;
      link.removeAttribute("aria-disabled");
      link.title = "Open the last job in Studio";
    } else {
      link.href = "#";
      link.setAttribute("aria-disabled", "true");
      link.title = "Studio opens once a job has finished";
    }
  }

  async function resolveStudioLink() {
    const remembered = readLast();
    if (remembered) { pointStudioAt(remembered); return; }
    pointStudioAt(null);
    try {
      const res = await fetch("/api/jobs", { headers: { "X-ASH-Client": "1" } });
      if (!res.ok) return;
      const jobs = await res.json();
      const done = jobs.find((j) => j.status === "done");
      if (done) pointStudioAt(done.id);
    } catch (err) { /* nav stays disabled; the page itself reports connection loss */ }
  }

  resolveStudioLink();
  window.AshNav = { rememberStudioJob, pointStudioAt };
})();
