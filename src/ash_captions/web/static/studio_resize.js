/* Draggable column widths for the Studio workspace.

   The three columns were fixed by media query, so an editor who wanted
   more room for the transcript had no way to ask for it -- on a 1440x900
   screen the video sat in a 648x745 box using 363px of it, and the words
   were squeezed into 480px beside all that empty mat. The balance an
   editor wants depends on the job (a talking head needs words, a reel
   needs picture), so it is theirs to set, not ours to guess.

   What this file adds: a drag handle either side of the words column,
   arrow-key resizing for the keyboard, double-click to put a column back
   to the width the stylesheet chose, and a remembered width per browser.

   Widths are written as inline custom properties on .workspace, which
   beats the media-query defaults -- so a preference set once survives a
   window resize instead of being overwritten by the next breakpoint. */
(function (root) {
  "use strict";

  var KEY = "ashstudio.columns.v1";
  var STEP = 24;          // one arrow press
  var MIN_EDIT = 260;     // below this the word toolbar starts wrapping badly
  var MIN_LOOKS = 200;    // a look card stops being readable under this
  var MIN_STAGE = 320;    // the video must stay judgeable, always
  var COLLAPSE_AT = 120;  // drag the looks below this and it closes

  // A drag ends with a click event on the same element. Without this the
   // click handler below undid every collapse the moment it happened.
  var suppressClick = false;

  // ---- pure helpers ----------------------------------------------------

  // What a drag is allowed to produce, given the room there is. The stage
  // floor is enforced here rather than in CSS so a drag stops dead at the
  // limit instead of the grid silently overflowing.
  function widthsFor(which, proposed, state, total) {
    var edit = state.edit;
    var looks = state.looks;
    if (which === "edit") edit = proposed;
    else looks = proposed;
    if (looks < COLLAPSE_AT && which === "looks") looks = 0;
    else looks = Math.max(MIN_LOOKS, looks);
    edit = Math.max(MIN_EDIT, edit);
    var over = edit + looks + MIN_STAGE - total;
    if (over > 0) {
      // Take it back off the column being dragged, never off the other.
      if (which === "edit") edit = Math.max(MIN_EDIT, edit - over);
      else if (looks > 0) looks = Math.max(MIN_LOOKS, looks - over);
    }
    return { edit: Math.round(edit), looks: Math.round(looks) };
  }

  // Everything above is arithmetic and is exported for tests; everything
  // below needs a document. look_card_ass.js splits the same way, and for
  // the same reason: the policy is the part worth guarding, and it should
  // be checkable without a browser.
  var api = {
    widthsFor: widthsFor, MIN_EDIT: MIN_EDIT, MIN_LOOKS: MIN_LOOKS,
    MIN_STAGE: MIN_STAGE, COLLAPSE_AT: COLLAPSE_AT, STEP: STEP,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AshStudioResize = api;

  if (typeof document === "undefined") return;
  var workspace = document.querySelector(".workspace");
  if (!workspace) return;
  var splitters = Array.prototype.slice.call(workspace.querySelectorAll(".splitter"));
  if (!splitters.length) return;

  // ---- state -----------------------------------------------------------

  function measured() {
    var cs = getComputedStyle(workspace);
    return {
      edit: parseFloat(cs.getPropertyValue("--edit-w")) || 480,
      looks: parseFloat(cs.getPropertyValue("--looks-w")) || 280,
    };
  }

  function apply(state, remember) {
    workspace.style.setProperty("--edit-w", state.edit + "px");
    workspace.style.setProperty("--looks-w", state.looks + "px");
    var closed = state.looks === 0;
    workspace.classList.toggle("looks-collapsed", closed);
    var looksHandle = workspace.querySelector('.splitter[data-splitter="looks"]');
    if (looksHandle) {
      looksHandle.title = closed
        ? "Click to bring the looks back"
        : "Drag to resize. Double-click to reset.";
      looksHandle.setAttribute("aria-label", closed ? "Show the looks column" : "Resize the looks column");
    }
    for (var i = 0; i < splitters.length; i++) {
      var s = splitters[i];
      var value = s.dataset.splitter === "edit" ? state.edit : state.looks;
      s.setAttribute("aria-valuenow", String(Math.round(value)));
    }
    if (remember) {
      try {
        localStorage.setItem(KEY, JSON.stringify(state));
      } catch (err) {
        /* private window, or storage full: the layout still works */
      }
    }
  }

  function restore() {
    var saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(KEY) || "null");
    } catch (err) {
      saved = null;
    }
    if (!saved || typeof saved.edit !== "number" || typeof saved.looks !== "number") return;
    // Re-clamp against *this* window: a width saved on a 1920 monitor
    // must not leave a 1024 laptop with no video.
    var total = workspace.clientWidth;
    var state = widthsFor("edit", saved.edit, { edit: saved.edit, looks: saved.looks }, total);
    if (saved.looks === 0) state.looks = 0;
    apply(state, false);
  }

  function reset(which) {
    // Drop the inline value and let the stylesheet's breakpoint answer.
    workspace.style.removeProperty(which === "edit" ? "--edit-w" : "--looks-w");
    workspace.classList.remove("looks-collapsed");
    var state = measured();
    apply(state, true);
  }

  // ---- dragging --------------------------------------------------------

  function startDrag(splitter, event) {
    if (event.button !== undefined && event.button !== 0) return;
    var which = splitter.dataset.splitter;
    var start = measured();
    var startX = event.clientX;
    var total = workspace.clientWidth;
    var latest = start;
    var moved = false;

    splitter.classList.add("dragging");
    document.body.classList.add("resizing");
    splitter.setPointerCapture && splitter.setPointerCapture(event.pointerId);

    function move(e) {
      // Both columns sit to the RIGHT of their handle, so dragging left
      // makes them wider. Reversing this is the bug that makes a splitter
      // feel broken, so it is the one line to read twice.
      var delta = startX - e.clientX;
      if (Math.abs(delta) > 3) moved = true;
      var base = which === "edit" ? start.edit : start.looks;
      latest = widthsFor(which, base + delta, start, total);
      apply(latest, false);
    }

    function end() {
      splitter.classList.remove("dragging");
      document.body.classList.remove("resizing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      suppressClick = moved;
      apply(latest, true);
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    event.preventDefault();
  }

  for (var i = 0; i < splitters.length; i++) {
    (function (splitter) {
      var which = splitter.dataset.splitter;
      splitter.setAttribute("aria-valuemin", String(which === "edit" ? MIN_EDIT : 0));
      splitter.addEventListener("pointerdown", function (e) { startDrag(splitter, e); });
      splitter.addEventListener("dblclick", function () { reset(which); });
      // A closed column reopens on a plain click. Requiring a drag to
      // undo what a drag did is fine; requiring one to discover it is not.
      splitter.addEventListener("click", function () {
        if (suppressClick) {
          suppressClick = false;
          return;
        }
        if (which !== "looks" || !workspace.classList.contains("looks-collapsed")) return;
        reset("looks");
      });
      splitter.addEventListener("keydown", function (e) {
        var delta = e.key === "ArrowLeft" ? STEP : e.key === "ArrowRight" ? -STEP : 0;
        if (e.key === "Home") {
          reset(which);
          e.preventDefault();
          return;
        }
        if (!delta) return;
        var state = measured();
        var base = which === "edit" ? state.edit : state.looks;
        apply(widthsFor(which, base + delta, state, workspace.clientWidth), true);
        e.preventDefault();
      });
    })(splitters[i]);
  }

  // A window that shrinks below what the saved widths allow gets them
  // clamped back, rather than pushing the video off its own column.
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      var state = measured();
      if (workspace.clientWidth <= 980) return;
      apply(widthsFor("edit", state.edit, state, workspace.clientWidth), false);
    }, 120);
  });

  restore();
})(typeof window !== "undefined" ? window : this);
