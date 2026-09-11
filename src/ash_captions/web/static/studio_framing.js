/* Reel framing: who each shot of a 9:16 reel follows, and changing it.

   The measurement this exists for: on the studio's own interview, 10 of
   24 shots held more than one person, so 42% of the reel was a choice
   the software made silently. It picks the largest person by matte mass,
   which is consistently the guest and never the interviewer -- a guess
   about framing, not a claim about who is speaking, because nothing in
   the pipeline listens to the audio.

   Only contested shots are listed. A shot with one person in it has
   nothing to choose between, and a list of thirty-six rows where four
   matter is a list nobody reads.

   Correcting is cheap: the saved plan carries every candidate's x, so
   re-aiming a window needs no footage and no model. A corrected re-burn
   skips the scan and is faster than the first one.

   The helpers above mount() are pure and exported for node, so
   tests/test_web/test_studio_framing.py runs them without a browser. */
(function () {
  "use strict";

  // ---- pure helpers (no DOM) ----

  // Where a candidate sits across the frame, as a word an editor can act
  // on. Thirds, because "left/middle/right" is how someone describes a
  // two-shot out loud.
  function zoneOf(x, sourceWidth) {
    if (!(sourceWidth > 0)) return "";
    const third = sourceWidth / 3;
    if (x < third) return "left";
    if (x < third * 2) return "middle";
    return "right";
  }

  // Labels for one window's candidates. Zones when they are distinct --
  // the case that actually happens, two people at opposite edges -- and
  // an ordinal fallback when two land in the same third, where "left"
  // twice would be worse than useless.
  function labelsFor(candidates, sourceWidth) {
    const zones = candidates.map((x) => zoneOf(x, sourceWidth));
    const distinct = new Set(zones).size === zones.length;
    if (distinct && zones.every(Boolean)) return zones;
    return candidates.map((_, i) => ordinal(i + 1) + " from the left");
  }

  function ordinal(n) {
    const names = ["", "1st", "2nd", "3rd", "4th", "5th", "6th"];
    return names[n] || n + "th";
  }

  function clockTime(seconds) {
    const total = Math.max(0, Math.round(seconds));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  // The windows worth showing: those where there was more than one
  // person, carrying their original index so an override names the right
  // window in the saved plan.
  function contestedWindows(plan) {
    if (!plan || !Array.isArray(plan.windows)) return [];
    const out = [];
    plan.windows.forEach((w, index) => {
      const candidates = Array.isArray(w.candidates) ? w.candidates : [];
      if (candidates.length > 1) out.push({ index, window: w, candidates });
    });
    return out;
  }

  // Only the choices that differ from what the plan already does. Sending
  // the rest would be harmless but makes a re-burn look like a change
  // when nothing changed.
  function changedOverrides(rows, picks) {
    const out = {};
    for (const row of rows) {
      const picked = picks[row.index];
      if (typeof picked === "number" && picked !== row.window.chosen) {
        out[String(row.index)] = picked;
      }
    }
    return out;
  }

  const exported = { zoneOf, labelsFor, clockTime, contestedWindows, changedOverrides, ordinal };

  // ---- the panel ----

  function mount(ctx) {
    const root = document.getElementById("framing");
    if (!root) return;
    const jobId = ctx.jobId;
    const picks = {};
    let plan = null;
    let rows = [];

    async function load() {
      let res;
      try {
        res = await AshApi.request("/api/jobs/" + encodeURIComponent(jobId) + "/reframe");
      } catch (err) {
        return; // offline; the tab simply stays hidden
      }
      if (!res.ok) return;
      plan = await res.json();
      // Not a reel: the tab stays absent rather than showing an empty one.
      if (!plan || plan.available === false) return;
      rows = contestedWindows(plan);
      render();
      root.hidden = false;
      if (typeof ctx.onAvailable === "function") ctx.onAvailable();
    }

    function render() {
      root.innerHTML = "";
      const head = document.createElement("p");
      head.className = "framing-head";
      if (!rows.length) {
        head.textContent =
          "Every shot in this reel had one person in it, so there was nothing to choose.";
        root.appendChild(head);
        return;
      }
      head.textContent =
        rows.length +
        (rows.length === 1 ? " shot had" : " shots had") +
        " more than one person. The reel follows the largest — click another to change it.";
      root.appendChild(head);

      const list = document.createElement("div");
      list.className = "framing-list";
      for (const row of rows) list.appendChild(rowEl(row));
      root.appendChild(list);

      const foot = document.createElement("div");
      foot.className = "framing-foot";
      const burn = document.createElement("button");
      burn.type = "button";
      burn.className = "btn primary small";
      burn.id = "framing-burn";
      burn.textContent = "Burn the reel again";
      burn.disabled = true;
      burn.addEventListener("click", reburn);
      foot.appendChild(burn);
      const note = document.createElement("span");
      note.className = "framing-note";
      note.textContent = "Re-burning with a change skips the scan, so it is quicker.";
      foot.appendChild(note);
      root.appendChild(foot);
      refreshBurnButton();
    }

    function rowEl(row) {
      const el = document.createElement("div");
      el.className = "framing-row";

      const when = document.createElement("button");
      when.type = "button";
      when.className = "framing-when";
      when.textContent = clockTime(row.window.start) + "–" + clockTime(row.window.end);
      when.title = "Play this shot";
      when.addEventListener("click", () => {
        if (ctx.player && typeof ctx.player.seek === "function") {
          ctx.player.seek(row.window.start + 0.1);
        }
      });
      el.appendChild(when);

      const choices = document.createElement("div");
      choices.className = "framing-choices";
      const labels = labelsFor(row.candidates, plan.source_width);
      labels.forEach((label, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn small framing-choice";
        b.textContent = label;
        const current = typeof picks[row.index] === "number" ? picks[row.index] : row.window.chosen;
        b.setAttribute("aria-pressed", String(i === current));
        b.addEventListener("click", () => {
          picks[row.index] = i;
          render();
        });
        choices.appendChild(b);
      });
      el.appendChild(choices);
      return el;
    }

    function refreshBurnButton() {
      const burn = document.getElementById("framing-burn");
      if (burn) burn.disabled = !Object.keys(changedOverrides(rows, picks)).length;
    }

    async function reburn() {
      const overrides = changedOverrides(rows, picks);
      if (!Object.keys(overrides).length) return;
      const burn = document.getElementById("framing-burn");
      if (burn) {
        burn.disabled = true;
        burn.textContent = "Queueing…";
      }
      try {
        const res = await AshApi.request("/api/jobs/" + encodeURIComponent(jobId) + "/burn", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            preset: ctx.job.options.preset,
            reframe: true,
            reframe_overrides: overrides,
          }),
        });
        if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't queue the burn"));
        AshToast.show("Re-burning the reel with your framing. Watch it in the queue.", {
          actions: [{ label: "Open the queue", href: "/" }],
          ms: 10000,
        });
      } catch (err) {
        AshToast.show(err.message, { kind: "bad" });
      } finally {
        if (burn) burn.textContent = "Burn the reel again";
        refreshBurnButton();
      }
    }

    load();
  }

  exported.mount = mount;
  if (typeof window !== "undefined") window.AshStudioFraming = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})();
