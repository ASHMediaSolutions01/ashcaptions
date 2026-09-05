/* The Sound tab of the style editor (v0.7 section 1).

   Its own file, and its own global, for the reason every v0.6 track had
   one: style_editor.js is near the 500-line limit this project holds
   itself to, and two people editing one form module is how the v0.5
   tracks ended up with two elements sharing a class name.

   The one thing this file insists on: every sound can be played from
   here, before it is chosen. Picking a sound you have never heard is the
   same mistake as picking a motion effect from a still picture, which is
   what v0.6 had to go back and fix on the look cards. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const triggerGroup = $("sound-trigger-group");
  const triggerHelp = $("sound-trigger-help");
  const settings = $("sound-settings");
  const library = $("sound-library");
  const emptyNote = $("sound-empty");
  const gainInput = $("sound-gain-input");
  const offsetInput = $("sound-offset-input");
  const spacingInput = $("sound-spacing-input");

  // Values match ash_captions.styles.schema's SOUND_TRIGGERS, which is
  // itself checked against engine.sfx.SfxTrigger by a test.
  const TRIGGERS = [
    ["off", "Never"],
    ["sentence", "Each sentence"],
    ["keyword", "Keywords"],
    ["both", "Sentences + keywords"],
    ["word", "Every word"],
  ];
  const TRIGGER_HELP = {
    off: "This look is silent.",
    sentence: "One sound on the first word of each sentence. The safe choice on a talking head.",
    keyword: "Only on the words in the client's keyword list — the same list the punch-in zoom uses, set on the Settings page.",
    both: "Sentence starts and keywords. A keyword inside a sentence does not fire twice.",
    word: "Every word. Only really works with a very short sound and a look that shows one word at a time.",
  };
  const MAX_SOUNDS = 4; // schema.py's _MAX_SOUNDS
  const DEFAULTS = { trigger: "off", sounds: [], gain_db: -8, offset_ms: 0, min_spacing_seconds: 0.35 };

  let available = []; // [{name, label, description, duration_seconds, url}]
  let getDraft = () => null;
  let playing = null;

  function sound() {
    const draft = getDraft();
    if (!draft) return null;
    if (!draft.sound) draft.sound = Object.assign({}, DEFAULTS);
    if (!Array.isArray(draft.sound.sounds)) draft.sound.sounds = [];
    return draft.sound;
  }

  // ---- playing one, at roughly the volume it will be burned at ----

  function preview(entry) {
    if (playing) { playing.pause(); playing = null; }
    const audio = new Audio(entry.url);
    const block = sound();
    // The look's gain, so what you hear here is what lands in the mix
    // rather than a full-scale version of it. Browsers take a 0-1
    // amplitude, hence the conversion from dB.
    const db = block ? Number(block.gain_db) : DEFAULTS.gain_db;
    audio.volume = Math.max(0, Math.min(1, Math.pow(10, (isFinite(db) ? db : 0) / 20)));
    playing = audio;
    audio.play().catch(() => { /* no audio device, or the tab is muted */ });
  }

  // ---- the library ----

  function renderLibrary() {
    const block = sound();
    const chosen = block ? block.sounds : [];
    library.replaceChildren();
    for (const entry of available) {
      const rank = chosen.indexOf(entry.name);
      const row = document.createElement("div");
      row.className = "sound-item" + (rank >= 0 ? " chosen" : "");

      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "sound-pick";
      pick.setAttribute("aria-pressed", rank >= 0 ? "true" : "false");
      pick.innerHTML =
        `<span class="sound-order">${rank >= 0 ? rank + 1 : ""}</span>` +
        `<span class="sound-name"></span><span class="sound-why hint"></span>`;
      pick.querySelector(".sound-name").textContent = entry.label;
      pick.querySelector(".sound-why").textContent = entry.description;
      pick.addEventListener("click", () => toggle(entry));

      const play = document.createElement("button");
      play.type = "button";
      play.className = "btn small sound-play";
      play.textContent = "Play";
      play.setAttribute("aria-label", `Play ${entry.label}`);
      play.addEventListener("click", () => preview(entry));

      row.append(pick, play);
      library.append(row);
    }
  }

  function toggle(entry) {
    const block = sound();
    if (!block) return;
    const at = block.sounds.indexOf(entry.name);
    if (at >= 0) {
      block.sounds.splice(at, 1);
      // Removing the last sound would leave a trigger firing nothing,
      // which the server rejects by name. Say so here instead.
      if (!block.sounds.length && block.trigger !== "off") {
        block.trigger = "off";
        syncTrigger();
      }
    } else {
      if (block.sounds.length >= MAX_SOUNDS) return;
      block.sounds.push(entry.name);
      preview(entry);
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
      input.name = "sound-trigger";
      input.value = value;
      input.addEventListener("change", () => {
        const block = sound();
        if (!block) return;
        if (value !== "off" && !block.sounds.length) {
          // Turning it on with nothing picked: choose the first sound so
          // the look is in a state the server will accept, rather than
          // failing on Save with a message about an empty list.
          if (available.length) block.sounds.push(available[0].name);
          renderLibrary();
        }
        block.trigger = value;
        renderSettings();
      });
      const text = document.createElement("span");
      text.textContent = label;
      wrap.append(input, text);
      triggerGroup.append(wrap);
    }
  }

  function syncTrigger() {
    const block = sound();
    const value = block ? block.trigger || "off" : "off";
    for (const input of triggerGroup.querySelectorAll("input")) input.checked = input.value === value;
    triggerHelp.textContent = TRIGGER_HELP[value] || "";
  }

  function renderSettings() {
    const block = sound();
    syncTrigger();
    settings.hidden = !block || (block.trigger || "off") === "off" || !available.length;
  }

  function number(input, key, fallback) {
    input.addEventListener("input", () => {
      const block = sound();
      if (!block) return;
      const value = Number(input.value);
      block[key] = isFinite(value) ? value : fallback;
    });
  }
  number(gainInput, "gain_db", DEFAULTS.gain_db);
  number(offsetInput, "offset_ms", DEFAULTS.offset_ms);
  number(spacingInput, "min_spacing_seconds", DEFAULTS.min_spacing_seconds);

  // ---- what style_editor.js calls ----

  function apply() {
    const block = sound();
    if (!block) return;
    gainInput.value = block.gain_db != null ? block.gain_db : DEFAULTS.gain_db;
    offsetInput.value = block.offset_ms != null ? block.offset_ms : DEFAULTS.offset_ms;
    spacingInput.value =
      block.min_spacing_seconds != null ? block.min_spacing_seconds : DEFAULTS.min_spacing_seconds;
    renderLibrary();
    renderSettings();
  }

  async function init(options) {
    getDraft = (options && options.getDraft) || getDraft;
    buildTriggers();
    try {
      const res = await AshApi.request("/api/sounds");
      available = res.ok ? await res.json() : [];
    } catch (err) {
      available = [];
    }
    // An empty library is a real answer, not a failure: a bundle built
    // before v0.7 carries no sounds, and the honest thing is to say so
    // rather than to offer names that would burn silent.
    emptyNote.hidden = available.length > 0;
    library.hidden = available.length === 0;
    apply();
  }

  window.AshStyleSound = { init, apply, defaults: () => Object.assign({}, DEFAULTS) };
})();
