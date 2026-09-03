/* The control page's client field and the glossary disclosure under it.
   A job's client picks that client's glossary file (`<glossary_dir>/
   <slug>.txt`) on top of the shared one; this file lets an editor pick the
   client (with the known ones suggested), remembers the last one used, and
   edits that client's glossary in place. Loaded before app.js, which reads
   AshClients.value() when it submits and AshClients.remember() after. */
(function () {
  "use strict";

  const LAST_CLIENT_KEY = "ash.lastClient";

  const clientInput = document.getElementById("client-input");
  const clientList = document.getElementById("client-list");
  const details = document.getElementById("glossary-details");
  const textarea = document.getElementById("glossary-text");
  const saveBtn = document.getElementById("glossary-save-btn");
  const status = document.getElementById("glossary-status");

  // Which client the textarea currently shows, so a save goes to the file
  // that was loaded even if the field was retyped meanwhile.
  let loadedFor = null;

  function value() {
    return clientInput ? clientInput.value.trim() : "";
  }

  function readLast() {
    try { return localStorage.getItem(LAST_CLIENT_KEY) || ""; } catch (err) { return ""; }
  }
  function remember() {
    try {
      const current = value();
      if (current) localStorage.setItem(LAST_CLIENT_KEY, current);
      else localStorage.removeItem(LAST_CLIENT_KEY);
    } catch (err) { /* private mode: the field still works for this visit */ }
  }

  async function refresh() {
    let names;
    try {
      const res = await AshApi.request("/api/clients");
      if (!res.ok) return;
      names = await res.json();
    } catch (err) {
      return;
    }
    clientList.innerHTML = "";
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      clientList.appendChild(opt);
    }
  }

  // ---- Glossary editing ----

  function setStatus(message, kind) {
    status.textContent = message;
    status.className = kind || "";
  }

  function glossaryUrl(name) {
    return `/api/clients/${encodeURIComponent(name)}/glossary`;
  }

  async function loadGlossary() {
    const name = value();
    if (!name) {
      loadedFor = null;
      textarea.value = "";
      textarea.disabled = true;
      saveBtn.disabled = true;
      setStatus("Type a client name above to edit its glossary.", "");
      return;
    }
    textarea.disabled = false;
    saveBtn.disabled = false;
    setStatus("Loading…", "");
    try {
      const res = await AshApi.request(glossaryUrl(name));
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't load the glossary"));
      const body = await res.json();
      loadedFor = body.client;
      textarea.value = body.text;
      setStatus(body.text ? `${body.slug}.txt` : `${body.slug}.txt (new — nothing saved yet)`, "");
    } catch (err) {
      loadedFor = null;
      setStatus(err.message, "err");
    }
  }

  async function saveGlossary() {
    const name = loadedFor || value();
    if (!name) return;
    saveBtn.disabled = true;
    setStatus("Saving…", "");
    try {
      const res = await AshApi.request(glossaryUrl(name), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: textarea.value }),
      });
      if (!res.ok) throw new Error(await AshApi.errorDetail(res, "Couldn't save the glossary"));
      const body = await res.json();
      loadedFor = body.client;
      textarea.value = body.text;
      setStatus(`Saved ${body.slug}.txt. It applies to every job for ${body.client} from now on.`, "ok");
      refresh(); // a new client now has a file, so it belongs in the list
    } catch (err) {
      setStatus(err.message, "err");
    } finally {
      saveBtn.disabled = false;
    }
  }

  // Reload when the client changes while the panel is open; the panel
  // itself loads on open so a closed one never fetches.
  let debounce = null;
  function onClientChanged() {
    if (!details.open) return;
    clearTimeout(debounce);
    debounce = setTimeout(loadGlossary, 250);
  }

  if (clientInput) {
    clientInput.value = readLast();
    clientInput.addEventListener("input", onClientChanged);
    clientInput.addEventListener("change", onClientChanged);
  }
  if (details) {
    details.addEventListener("toggle", () => { if (details.open) loadGlossary(); });
    saveBtn.addEventListener("click", saveGlossary);
  }
  refresh();

  window.AshClients = { value, remember, refresh };
})();
