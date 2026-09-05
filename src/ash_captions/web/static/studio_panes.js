/* Words / Check as two tabs of one panel, not two panels stacked.

   The two show the same lines until something has been translated -- the
   editable transcript from v0.6 and the caption-check panel from v0.5 --
   and stacked they took two thirds of the Studio between them to say the
   same thing twice, which is what left the video 105px tall on a laptop.

   Nothing here reaches into either script. The panes are chosen with a
   `data-pane` attribute on their container and CSS does the hiding, so a
   pane the other script has hidden with the `hidden` attribute stays
   hidden either way and neither has to know this file exists.

   The Check tab is disabled while there is nothing to check, and carries
   a dot when the transcriber was unsure of a word in there: the reason to
   open a tab has to be visible from the other one. */
(function () {
  "use strict";

  const panes = document.getElementById("panes");
  const bar = document.getElementById("pane-tabs");
  const words = document.getElementById("transcript-edit");
  const check = document.getElementById("check");
  if (!panes || !bar || !words || !check) return;

  const TABS = [
    { pane: "words", label: "Words", el: words },
    { pane: "check", label: "Check", el: check },
  ];
  const buttons = new Map();

  function available(tab) {
    // `hidden` is how both scripts say "there is nothing here" -- no
    // transcript at all, or a job whose source is gone.
    return !tab.el.hidden;
  }

  function select(pane) {
    const tab = TABS.find((t) => t.pane === pane);
    if (!tab || !available(tab)) return;
    panes.dataset.pane = pane;
    for (const [name, button] of buttons) {
      button.setAttribute("aria-selected", name === pane ? "true" : "false");
      button.tabIndex = name === pane ? 0 : -1;
    }
  }

  function unsureCount() {
    // The check panel's own chip, which it writes as "N uncertain words".
    const chip = check.querySelector(".check-chip");
    if (!chip || chip.disabled) return 0;
    const match = /(\d+)/.exec(chip.textContent || "");
    return match ? Number(match[1]) : 0;
  }

  function sync() {
    let selected = panes.dataset.pane;
    for (const tab of TABS) {
      const button = buttons.get(tab.pane);
      const ok = available(tab);
      button.disabled = !ok;
      button.hidden = !ok && tab.pane === "check" && !check.childElementCount;
      if (!ok && selected === tab.pane) selected = null;
    }
    const dot = buttons.get("check").querySelector(".pane-dot");
    dot.hidden = unsureCount() === 0;
    // Fall back to whichever pane is actually there, so the panel is never
    // blank because the pane it was showing went away.
    if (!selected) {
      const first = TABS.find(available);
      if (first) select(first.pane);
    } else {
      select(selected);
    }
  }

  for (const tab of TABS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pane-tab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", tab.pane === "words" ? "true" : "false");
    button.textContent = tab.label;
    const dot = document.createElement("span");
    dot.className = "pane-dot";
    dot.textContent = "•";
    dot.hidden = true;
    dot.setAttribute("aria-hidden", "true");
    button.append(dot);
    button.addEventListener("click", () => select(tab.pane));
    button.addEventListener("keydown", (e) => {
      const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!delta) return;
      e.preventDefault();
      const order = TABS.filter(available);
      const at = order.findIndex((t) => t.pane === panes.dataset.pane);
      const next = order[(at + delta + order.length) % order.length];
      if (next) { select(next.pane); buttons.get(next.pane).focus(); }
    });
    bar.append(button);
    buttons.set(tab.pane, button);
  }

  // Both panels mount and change long after this file runs, so their
  // availability is watched rather than read once.
  const watch = new MutationObserver(sync);
  watch.observe(check, { attributes: true, attributeFilter: ["hidden"], childList: true, subtree: true });
  watch.observe(words, { attributes: true, attributeFilter: ["hidden"], childList: true });
  sync();

  window.AshStudioPanes = { select, sync };
})();
