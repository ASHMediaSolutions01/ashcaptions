/* Toasts and desktop notifications, shared by the control page and the
   Studio. AshToast.show(text, {kind, actions:[{label, href|onClick}], ms})
   stacks at the bottom right and never blocks; AshNotify fires a browser
   Notification when the person has said yes, asking once and quietly. */
(function () {
  "use strict";

  let stack = null;

  function ensureStack() {
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      stack.setAttribute("aria-live", "polite");
      document.body.appendChild(stack);
    }
    return stack;
  }

  function show(text, options) {
    const { kind, actions, ms } = options || {};
    const toast = document.createElement("div");
    toast.className = `toast${kind ? ` ${kind}` : ""}`;
    toast.setAttribute("role", "status");

    const body = document.createElement("div");
    body.className = "toast-text";
    body.textContent = text;
    toast.appendChild(body);

    if (actions && actions.length) {
      const row = document.createElement("div");
      row.className = "toast-actions";
      for (const action of actions) {
        const el = document.createElement(action.href ? "a" : "button");
        el.className = "btn small";
        el.textContent = action.label;
        if (action.href) el.href = action.href;
        else el.type = "button";
        el.addEventListener("click", () => {
          if (action.onClick) action.onClick();
          if (!action.keep) dismiss();
        });
        row.appendChild(el);
      }
      toast.appendChild(row);
    }

    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", dismiss);
    toast.appendChild(close);

    let timer = ms === 0 ? null : setTimeout(dismiss, ms || 7000);
    function dismiss() {
      if (timer) clearTimeout(timer);
      timer = null;
      toast.remove();
    }
    ensureStack().appendChild(toast);
    return { dismiss };
  }

  // ---- Desktop notifications ----
  // Asked for once, only after the first submit (a permission prompt on
  // page load is noise); a refusal is respected and never re-asked here.

  function supported() {
    return typeof Notification !== "undefined";
  }

  function requestOnce() {
    if (!supported() || Notification.permission !== "default") return;
    try { Notification.requestPermission().catch(() => {}); } catch (err) { /* older API */ }
  }

  function notify(title, body, onClick) {
    if (!supported() || Notification.permission !== "granted") return null;
    try {
      const n = new Notification(title, { body, icon: "/static/favicon.svg", tag: `ash-${title}-${body}` });
      if (onClick) n.onclick = () => { window.focus(); onClick(); n.close(); };
      return n;
    } catch (err) {
      return null;
    }
  }

  window.AshToast = { show };
  window.AshNotify = { requestOnce, notify, granted: () => supported() && Notification.permission === "granted" };
})();
