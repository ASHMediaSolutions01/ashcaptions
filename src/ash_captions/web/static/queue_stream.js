/* The stream: which jobs sit at the top as "recent", which fold under
   "Earlier", how the search reaches all of them, and paging past what the
   live snapshot carries. Cards themselves are queue.js; this module only
   decides which list each one belongs in. Loaded before app.js, which
   feeds it every snapshot through AshStream.onSnapshot(jobs). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const recentList = $("job-list");
  const earlier = $("earlier");
  const earlierList = $("earlier-list");
  const earlierCount = $("earlier-count");
  const earlierMore = $("earlier-more");
  const emptyQueue = $("empty-queue");
  const streamStatus = $("stream-status");
  const searchInput = $("search-input");

  const DAY = 24 * 60 * 60 * 1000;
  const RECENT_MAX_FINISHED = 8; // finished jobs kept at the top
  const RECENT_MIN_FINISHED = 3; // even when older than a day
  const PAGE = 20;
  const SNAPSHOT_LIMIT = 100; // routes_jobs.JOB_LIST_LIMIT: the SSE snapshot's cap
  const SEARCH_REFRESH_MS = 1500;

  let snapshot = [];
  let earlierShown = PAGE; // how many "Earlier" cards are open
  let beyond = []; // jobs fetched past the snapshot, oldest last
  let beyondExhausted = false;
  let query = "";
  let searchTimer = null;
  let lastSearchAt = 0;
  let searchSeq = 0;

  function finished(job) { return job.status === "done" || job.status === "failed"; }
  function when(job) { const t = Date.parse(job.updated_at || job.created_at); return Number.isNaN(t) ? 0 : t; }

  // Live jobs always lead. Finished ones stay at the top while they are
  // from the last day, at most eight of them; a quiet desk keeps its last
  // three so the page never opens on nothing but a rule.
  function partition(jobs) {
    const live = jobs.filter((j) => !finished(j));
    const done = jobs.filter(finished);
    const now = Date.now();
    let recentDone = done.filter((j) => now - when(j) < DAY).slice(0, RECENT_MAX_FINISHED);
    if (recentDone.length < RECENT_MIN_FINISHED) recentDone = done.slice(0, RECENT_MIN_FINISHED);
    const keep = new Set(recentDone.map((j) => j.id));
    return { recent: live.concat(recentDone), earlier: done.filter((j) => !keep.has(j.id)) };
  }

  function setStatus(text) {
    streamStatus.textContent = text || "";
    streamStatus.hidden = !text;
  }

  function renderStream() {
    if (query) return; // the search owns the lists while it is active
    const { recent, earlier: older } = partition(snapshot);
    const seen = new Set(snapshot.map((j) => j.id));
    const all = older.concat(beyond.filter((j) => !seen.has(j.id)));
    const shown = all.slice(0, earlierShown);
    AshQueue.renderInto(recentList, recent);
    AshQueue.renderInto(earlierList, shown);
    AshQueue.prune();
    emptyQueue.hidden = snapshot.length > 0;
    earlier.hidden = all.length === 0;
    // More to open from what is loaded, or more to fetch past the snapshot.
    const canFetch = !beyondExhausted && snapshot.length >= SNAPSHOT_LIMIT;
    const total = `${all.length}${canFetch ? "+" : ""}`;
    earlierCount.textContent = all.length === shown.length && !canFetch ? `${all.length} job${all.length === 1 ? "" : "s"}` : `${shown.length} of ${total} jobs`;
    earlierMore.hidden = shown.length >= all.length && !canFetch;
    setStatus("");
  }

  async function showMore() {
    earlierShown += PAGE;
    const { earlier: older } = partition(snapshot);
    const loaded = older.length + beyond.length;
    if (earlierShown > loaded && !beyondExhausted && snapshot.length >= SNAPSHOT_LIMIT) {
      earlierMore.disabled = true;
      try {
        const offset = snapshot.length + beyond.length;
        const page = await AshApp.loadJson(`/api/jobs?offset=${offset}&limit=${PAGE}`, "older jobs");
        beyond = beyond.concat(page);
        if (page.length < PAGE) beyondExhausted = true;
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
      } finally {
        earlierMore.disabled = false;
      }
    }
    renderStream();
  }
  earlierMore.addEventListener("click", showMore);

  // ---- Search: one list of matches, from the whole history ----

  async function runSearch() {
    const q = query;
    const seq = ++searchSeq;
    lastSearchAt = Date.now();
    let matches;
    try {
      matches = await AshApp.loadJson(`/api/jobs?q=${encodeURIComponent(q)}&limit=${SNAPSHOT_LIMIT}`, "the search");
    } catch (err) {
      setStatus(err.message);
      return;
    }
    if (seq !== searchSeq || q !== query) return; // a newer search has taken over
    AshQueue.renderInto(recentList, matches);
    AshQueue.renderInto(earlierList, []);
    AshQueue.prune();
    earlier.hidden = true;
    emptyQueue.hidden = true;
    setStatus(matches.length === 0 ? `Nothing matches "${q}".` : `${matches.length}${matches.length >= SNAPSHOT_LIMIT ? "+" : ""} match${matches.length === 1 ? "" : "es"} for "${q}".`);
  }

  function setQuery(value) {
    const next = value.trim();
    if (next === query) return;
    query = next;
    if (!query) { renderStream(); return; }
    runSearch();
  }

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => setQuery(searchInput.value), 250);
  });
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { searchInput.value = ""; setQuery(""); searchInput.blur(); }
  });
  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (e.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(tag)) { e.preventDefault(); searchInput.focus(); searchInput.select(); }
  });

  // ---- Every snapshot from app.js ----

  function onSnapshot(jobs) {
    snapshot = jobs || [];
    if (query) {
      // Matches keep moving (a running job's progress) without a keystroke.
      if (Date.now() - lastSearchAt > SEARCH_REFRESH_MS) runSearch();
      return;
    }
    renderStream();
  }

  window.AshStream = { onSnapshot, partition };
})();
