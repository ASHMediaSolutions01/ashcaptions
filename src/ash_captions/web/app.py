"""FastAPI application factory for the ASH Captions control page.

This module only assembles the app: it wires the injected dependencies
onto `app.state`, mounts the routers (`routes_jobs.py`, `routes_events.py`,
`routes_styles.py`, `routes_updates.py`, `routes_review.py`), installs the
Origin/Host defence (`security.py`), and serves the two HTML pages. The job
queue and the language catalogue are injected (see `interfaces.py`) so
this module never imports `ash_captions.engine`, `ash_captions.languages`,
or any storage/pipeline code directly -- that keeps it testable with fakes
and keeps ownership of those modules with whoever builds them.
`ash_captions.styles`/`ash_captions.app.updater` are never imported here
either -- see `styles_adapter.py`/`preview_adapter.py`/`update_adapter.py`,
the modules that are allowed to.

Binding: this app must only ever be served on 127.0.0.1. It is a
single-user, offline, LAN-invisible tool (spec §4.4, §5) -- there is no auth
layer because there is nothing to authenticate against; `security.py`
closes the one hole that leaves (other pages in the editor's own browser).
`run_server()` below is the sanctioned way to start it and refuses any
other host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ash_captions.config import DEFAULT_PORT, data_root

from .interfaces import (
    JobQueue,
    LanguageCatalogueProvider,
    PreviewRenderer,
    StyleProvider,
    UpdateApplier,
)
from .routes_events import DEFAULT_SSE_POLL_INTERVAL, build_events_router
from .routes_jobs import build_jobs_router
from .routes_review import build_review_router
from .routes_studio import build_studio_router
from .routes_styles import build_styles_router
from .routes_updates import build_update_router
from .runtime import app_version
from .runtime import updates_supported as _runtime_updates_supported
from .security import install_security_middleware

STATIC_DIR = Path(__file__).parent / "static"
# Placeholder in the HTML files' asset URLs, replaced with the running
# version so a shipped update never serves last release's app.js against
# this release's page from the browser cache.
VERSION_PLACEHOLDER = "__VERSION__"


def default_incoming_dir() -> Path:
    """Where uploads land: under the data root (`ASH_CAPTIONS_ROOT` aware),
    distinct from the watch folder so an upload is never picked up twice."""
    return data_root() / "web_uploads"


def create_app(
    queue: JobQueue,
    catalogue: LanguageCatalogueProvider,
    *,
    style_provider: StyleProvider | None = None,
    preview_renderer: PreviewRenderer | None = None,
    update_applier: UpdateApplier | None = None,
    incoming_dir: Path | None = None,
    sse_poll_interval: float = DEFAULT_SSE_POLL_INTERVAL,
    updates_supported: Callable[[], bool] | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    `incoming_dir` is where files uploaded through the control page are
    written before being handed to `queue.submit()`; defaults to
    `data_root()/web_uploads` (see `default_incoming_dir`). Production
    passes `settings.upload_dir` here.

    `style_provider`/`preview_renderer`/`update_applier` default to real
    implementations backed by `ash_captions.styles`/`ash_captions.engine`/
    `ash_captions.app.updater` (see `styles_adapter.py`/`preview_adapter.py`/
    `update_adapter.py`) so production callers don't need to change to get
    the style editor (spec 7A) or in-app updates (spec 11.4) working --
    tests inject fakes instead, same as `queue`/`catalogue`.

    `updates_supported` defaults to `runtime.updates_supported` -- "this is
    an installed build, not a source checkout". Tests inject a lambda.

    Note `update_applier` only covers *applying* an update. *Checking* for
    one is owned by whoever calls this (`app/__main__.py` in production),
    which sets `app.state.update_state` itself, after this returns, to
    whatever `ash_captions.app.updater.check_for_update_in_background`
    populates -- this function does not touch that attribute at all, and
    `routes_updates.py` treats its absence (e.g. in a test that never sets
    it) as a normal "no update" outcome, not an error.
    """
    app = FastAPI(title="ASH Captions")
    app.state.queue = queue
    app.state.catalogue = catalogue
    app.state.style_provider = style_provider or _default_style_provider()
    app.state.preview_renderer = preview_renderer or _default_preview_renderer()
    app.state.update_applier = update_applier or _default_update_applier()
    app.state.incoming_dir = incoming_dir or default_incoming_dir()
    app.state.sse_poll_interval = sse_poll_interval
    app.state.version = app_version()

    def get_queue(request: Request) -> JobQueue:
        return request.app.state.queue

    def get_catalogue(request: Request) -> LanguageCatalogueProvider:
        return request.app.state.catalogue

    def get_style_provider(request: Request) -> StyleProvider:
        return request.app.state.style_provider

    def get_preview_renderer(request: Request) -> PreviewRenderer:
        return request.app.state.preview_renderer

    def get_update_applier(request: Request) -> UpdateApplier:
        return request.app.state.update_applier

    install_security_middleware(app)

    # Serves style.css and app.js alongside the page. index.html is served
    # from "/" below (not through this mount) so the control page works at
    # the bare root URL.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(build_jobs_router(get_queue, get_catalogue, get_style_provider))
    app.include_router(build_events_router(get_queue))
    app.include_router(build_review_router(get_queue))
    app.include_router(build_studio_router(get_queue, get_style_provider))
    app.include_router(build_styles_router(get_style_provider, get_preview_renderer))
    app.include_router(
        build_update_router(get_queue, get_update_applier, updates_supported or _runtime_updates_supported)
    )

    pages = {
        name: render_page(STATIC_DIR / name, app.state.version)
        for name in ("index.html", "style_editor.html", "guide.html", "studio.html")
    }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(pages["index.html"])

    @app.get("/style-editor", response_class=HTMLResponse)
    async def style_editor_page() -> HTMLResponse:
        return HTMLResponse(pages["style_editor.html"])

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        # Browsers ask for /favicon.ico unprompted; a 404 there was the one
        # console error on every page.
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/guide", response_class=HTMLResponse)
    async def guide_page() -> HTMLResponse:
        # The editor's guide, served inside the app so it is always the
        # version that matches the UI the editor is looking at.
        return HTMLResponse(pages["guide.html"])

    @app.get("/studio/{job_id}", response_class=HTMLResponse)
    async def studio_page(job_id: str) -> HTMLResponse:
        # One static page for every job: studio.js reads the id from the
        # URL and fetches the job, so an unknown or unfinished job gets a
        # message with a way back to the queue rather than a bare 404.
        return HTMLResponse(pages["studio.html"])

    return app


def render_page(path: Path, version: str) -> str:
    """Read an HTML page once and stamp the asset cache-buster into it."""
    return path.read_text(encoding="utf-8").replace(VERSION_PLACEHOLDER, version)


def _default_style_provider() -> StyleProvider:
    """Deferred import so importing `app.py` never requires
    `ash_captions.styles` to already be constructed -- mirrors `run_server`'s
    lazy `import uvicorn` below."""
    from .styles_adapter import StylesPackageAdapter

    return StylesPackageAdapter()


def _default_preview_renderer() -> PreviewRenderer:
    from .preview_adapter import InProcessPreviewRenderer

    return InProcessPreviewRenderer()


def _default_update_applier() -> UpdateApplier:
    from .update_adapter import UpdaterAdapter

    return UpdaterAdapter()


def run_server(app: FastAPI, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    """Serve `app`. Refuses anything but 127.0.0.1 -- this tool must never be
    reachable from the LAN (spec §4.4, §5). Access logging is off: a
    progress-polling page would otherwise write a line per second into the
    app log for the length of every job."""
    if host != "127.0.0.1":
        raise ValueError(
            f"ASH Captions must bind to 127.0.0.1 only; refusing host {host!r}."
        )
    import uvicorn

    uvicorn.run(app, host=host, port=port, access_log=False)
