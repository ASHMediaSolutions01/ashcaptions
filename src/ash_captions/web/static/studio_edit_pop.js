/* Where the word popup goes (studio_edit.js's editor for one word).

   Its own file because the geometry is a separate concern from the
   editing, and because it is the part that has to know about the page
   around it: the popup is positioned in the document rather than inside
   the scrolling list, so that the list cannot clip it -- which means
   everything the list sits in has to be taken into account here.

   Three rules, each of them a bug that was found by opening the Studio
   rather than by reading the code:

   * Below the word, or above it when below would run off the bottom of
     the window. On the last lines of a long transcript the buttons were
     simply unreachable.
   * Never off the left or right edge either.
   * Placed again after the browser has reflowed. Selecting a word also
     reveals the word toolbar, and the popup that was placed a moment
     earlier ended up 40px out, sitting over the toolbar. */
(function (root) {
  "use strict";

  const MARGIN = 8;
  const GAP = 6;

  function place(pop, span) {
    if (!pop || !span) return;
    const box = span.getBoundingClientRect();
    const width = pop.offsetWidth || 280;
    const height = pop.offsetHeight || 150;
    const left = Math.max(MARGIN, Math.min(window.innerWidth - width - MARGIN, box.left + window.scrollX));
    let top = box.bottom + GAP;
    if (top + height > window.innerHeight - MARGIN) {
      top = Math.max(MARGIN, box.top - height - GAP);
    }
    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(top + window.scrollY)}px`;
  }

  /* Keep it under its word for as long as it is open. The popup is
     positioned in the document, so every scroller it sits over -- the
     words column, the list inside it -- has to move it, or it detaches
     from the word it belongs to the moment you scroll. */
  function follow(pop, currentSpan, scrollers) {
    const again = () => {
      const span = currentSpan();
      if (span && !pop.hidden) place(pop, span);
    };
    window.addEventListener("resize", again);
    for (const el of scrollers || []) {
      if (el) el.addEventListener("scroll", again, { passive: true });
    }
    return again;
  }

  const api = { place, follow, MARGIN, GAP };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AshEditPopup = api;
})(typeof window !== "undefined" ? window : this);
