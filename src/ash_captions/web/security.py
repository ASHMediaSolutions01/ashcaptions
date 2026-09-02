"""Origin / Host defence for a loopback-only control page.

The app binds to 127.0.0.1 with no auth layer (spec §4.4, §5): nothing on
the LAN can reach it. But the editor's own browser can -- and so can any
web page open in that browser. Without the checks here, an arbitrary site
could ``fetch("http://127.0.0.1:8756/api/jobs/by-path", {method: "POST"})``
or submit a hidden form to enqueue jobs or start the update flow. Two
cheap, layered defences close that:

1. ``TrustedHostMiddleware`` (Starlette's own) rejects any request whose
   ``Host`` isn't ``127.0.0.1``/``localhost`` -- DNS-rebinding style tricks
   get a 400 before a route ever runs.

2. ``ClientOriginMiddleware`` (below) rejects every mutating request
   (anything but GET/HEAD/OPTIONS) that either carries an ``Origin`` header
   for a host other than ``127.0.0.1``/``localhost`` (any port -- the app
   probes upward from its default), or lacks the ``X-ASH-Client: 1``
   header. A cross-site *form* post has a foreign ``Origin`` and no custom
   header; a cross-site ``fetch`` with the custom header triggers a CORS
   preflight this app never answers, so the browser blocks it. The app's
   own ``app.js``/``style_editor.js`` send the header on every mutating
   call.

Rejections use the same ``{"detail": ...}`` JSON shape as ``HTTPException``
so the front-end's error path (``body.detail``) reads them unchanged.
"""
from __future__ import annotations

import re

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CLIENT_HEADER = "x-ash-client"
CLIENT_HEADER_VALUE = "1"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d{1,5})?$", re.IGNORECASE)

FOREIGN_ORIGIN_DETAIL = "Requests from other web pages are not allowed."
MISSING_CLIENT_HEADER_DETAIL = (
    f"Missing the {CLIENT_HEADER.upper()} header -- only the ASH Captions page may change things."
)


def is_local_origin(origin: str) -> bool:
    return _LOCAL_ORIGIN.match(origin.strip()) is not None


class ClientOriginMiddleware:
    """Pure ASGI (not ``BaseHTTPMiddleware``) so the SSE stream and large
    uploads pass through untouched -- ``BaseHTTPMiddleware`` buffers and
    re-wraps response bodies, which breaks long-lived streaming."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        origin = headers.get("origin")
        if origin is not None and not is_local_origin(origin):
            await JSONResponse({"detail": FOREIGN_ORIGIN_DETAIL}, status_code=403)(scope, receive, send)
            return
        if headers.get(CLIENT_HEADER, "").strip() != CLIENT_HEADER_VALUE:
            await JSONResponse({"detail": MISSING_CLIENT_HEADER_DETAIL}, status_code=403)(scope, receive, send)
            return

        await self.app(scope, receive, send)


def install_security_middleware(app) -> None:
    """Attach both layers to a FastAPI/Starlette app. Called from
    ``create_app()``; kept here so the policy lives in one place."""
    app.add_middleware(ClientOriginMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
