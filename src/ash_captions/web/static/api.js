/* Shared by app.js, updates.js and style_editor.js: every request to the
   app goes through AshApi.request so it carries the X-ASH-Client header
   the server requires on anything that changes state (web/security.py).
   A cross-site page can't add that header without a CORS preflight the
   app never answers, which is the whole point. */
(function () {
  "use strict";

  const CLIENT_HEADERS = { "X-ASH-Client": "1" };

  function request(url, options) {
    const opts = Object.assign({}, options || {});
    opts.headers = Object.assign({}, CLIENT_HEADERS, opts.headers || {});
    return fetch(url, opts);
  }

  // The server's {"detail": "..."} message when it sent one, otherwise a
  // fallback with the HTTP status so a failure is never a silent nothing.
  async function errorDetail(res, fallback) {
    const body = await res.json().catch(() => ({}));
    return body.detail || `${fallback} (HTTP ${res.status})`;
  }

  window.AshApi = { request, errorDetail };
})();
