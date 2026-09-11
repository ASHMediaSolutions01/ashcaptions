/* The Emoji tab of the style editor (v0.9).

   A near-copy of style_editor_sound.js, on purpose: an editor who has
   used one should not have to learn the other. Pick up to four, they
   cycle in the order you picked them, and the trigger is the same list
   of words punch-in and sound already use.

   Where it deliberately differs: a sound has a Play button because you
   cannot see it, and choosing one you have never heard is the mistake
   v0.6 had to go back and fix on the look cards. An emoji is a picture,
   so the picker simply shows it and there is nothing to audition. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const triggerGroup = $("emoji-trigger-group");
  const triggerHelp = $("emoji-trigger-help");
  const settings = $("emoji-settings");
  const library = $("emoji-library");
  const emptyNote = $("emoji-empty");
  const spacingInput = $("emoji-spacing-input");

  // Values match ash_captions.styles.schema's EMOJI_TRIGGERS, which is
  // itself checked against engine.stickers by a test. There is no
  // "every word" here where sound has one: a picture on every word is
  // confetti, and the engine has no such trigger to offer.
  const TRIGGERS = [
    ["off", "Never"],
    ["sentence", "Each sentence"],
    ["keyword", "Keywords"],
    ["both", "Sentences + keywords"],
  ];
  const TRIGGER_HELP = {
    off: "This look throws no emoji.",
    sentence: "One emoji on the first word of each sentence. Busy on fast dialogue — the least gap below is what keeps it watchable.",
    keyword: "Only on the words in the client's keyword list — the same list the punch-in zoom and the sounds use, set on the Settings page.",
    both: "Sentence starts and keywords. A keyword inside a sentence does not fire twice.",
  };
  const MAX_EMOJI = 4; // schema.py's _MAX_EMOJI
  const DEFAULTS = { trigger: "off", emoji: [], min_spacing_seconds: 2.5 };

  let available = []; // [{name, label, url}]
  let getDraft = () => null;

  function block() {
    const draft = getDraft();
    if (!draft) return null;
    if (!draft.emoji) draft.emoji = Object.assign({}, DEFAULTS, { emoji: [] });
    if (!Array.isArray(draft.emoji.emoji)) draft.emoji.emoji = [];
    return draft.emoji;
  }

  // ---- the library ----

  function renderLibrary() {
    const chosen = block() ? block().emoji : [];
    library.replaceChildren();
    for (const entry of available) {
      const rank = chosen.indexOf(entry.name);
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "emoji-pick" + (rank >= 0 ? " chosen" : "");
      pick.setAttribute("aria-pressed", rank >= 0 ? "true" : "false");

      const img = document.createElement("img");
      img.src = entry.url;
      img.alt = "";
      img.className = "emoji-art";
      const name = document.createElement("span");
      name.className = "emoji-name";
      name.textContent = entry.label;
      const order = document.createElement("span");
      order.className = "emoji-order";
      order.textContent = rank >= 0 ? String(rank + 1) : "";

      pick.append(order, img, name);
      pick.setAttribute("aria-label", entry.label);
      pick.addEventListener("click", () => toggle(entry));
      library.append(pick);
    }
  }

  function toggle(entry) {
    const state = block();
    if (!state) return;
    const at = state.emoji.indexOf(entry.name);
    if (at >= 0) {
      state.emoji.splice(at, 1);
      // Removing the last one would leave a trigger firing nothing,
      // which the server rejects by name. Say so here instead.
      if (!state.emoji.length && state.trigger !== "off") {
        state.trigger = "off";
      }
    } else {
      if (state.emoji.length >= MAX_EMOJI) return;
      state.emoji.push(entry.name);
    }
    renderLibrary();
    renderSettings();
  }

  // ---- the trigger ----

  function buildTriggers() {
    triggerGroup.replaceChildren();
    for (const [value, label] of TRIGGERS) {
      const wrap = document.createElement("label");
      wrap.className = "radio";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "emoji-trigger";
      input.value = value;
      input.addEventListener("change", () => {
        const state = block();
        if (!state) return;
        if (value !== "off" && !state.emoji.length) {
          // Turning it on with nothing picked: choose the first one so
          // the look is in a state the server will accept, rather than
          // failing on Save with a message about an empty list.
          if (available.length) state.emoji.push(available[0].name);
          renderLibrary();
        }
        state.trigger = value;
        renderSettings();
      });
      const text = document.createElement("span");
      text.textContent = label;
      wrap.append(input, text);
      triggerGroup.append(wrap);
    }
  }

  function syncTrigger() {
    const state = block();
    const value = state ? state.trigger || "off" : "off";
    for (const input of triggerGroup.querySelectorAll("input")) input.checked = input.value === value;
    triggerHelp.textContent = TRIGGER_HELP[value] || "";
  }

  function renderSettings() {
    const state = block();
    syncTrigger();
    settings.hidden = !state || (state.trigger || "off") === "off" || !available.length;
  }

  spacingInput.addEventListener("input", () => {
    const state = block();
    if (!state) return;
    const value = Number(spacingInput.value);
    state.min_spacing_seconds = isFinite(value) ? value : DEFAULTS.min_spacing_seconds;
  });

  // ---- what style_editor.js calls ----

  function apply() {
    const state = block();
    if (!state) return;
    spacingInput.value =
      state.min_spacing_seconds != null ? state.min_spacing_seconds : DEFAULTS.min_spacing_seconds;
    renderLibrary();
    renderSettings();
  }

  async function init(options) {
    getDraft = (options && options.getDraft) || getDraft;
    buildTriggers();
    try {
      const res = await AshApi.request("/api/emoji");
      available = res.ok ? await res.json() : [];
    } catch (err) {
      available = [];
    }
    // An empty library is a real answer, not a failure: a bundle built
    // before v0.9 carries no emoji, and the honest thing is to say so
    // rather than to offer names that would burn nothing.
    emptyNote.hidden = available.length > 0;
    library.hidden = available.length === 0;
    apply();
  }

  window.AshStyleEmoji = { init, apply, defaults: () => Object.assign({}, DEFAULTS, { emoji: [] }) };
})();
