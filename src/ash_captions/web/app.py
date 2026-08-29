"""FastAPI application factory for the ASH Captions control page.

This module owns the HTTP surface only. The job queue and the language
catalogue are injected (see `interfaces.py`) so this module never imports
`ash_captions.engine`, `ash_captions.languages`, or any storage/pipeline
code directly -- that keeps it testable with fakes and keeps ownership of
those modules with whoever builds them.

Binding: this app must only ever be served on 127.0.0.1. It is a
single-user, offline, LAN-invisible tool (spec §4.4, §5) -- there is no auth
layer because there is nothing to authenticate against. `run_server()` below
is the sanctioned way to start it and refuses any other host.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi import Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .interfaces import (
    JobNotFoundError,
    JobNotRetryableError,
    JobQueue,
    LanguageCatalogueProvider,
    PreviewNotFoundError,
    PreviewRenderer,
    StyleIsShippedError,
    StyleNotFoundError,
    StyleProvider,
    StyleValidationFailedError,
    UpdateApplier,
    UpdateApplyNotFoundError,
)
from .models import (
    ALLOWED_VIDEO_EXTENSIONS,
    Job,
    JobOptions,
    JobPathRequest,
    JobStatus,
    Language,
    PreviewJob,
    PreviewRequest,
    PreviewStatus,
    StyleSummary,
    UpdateApplyJob,
    UpdateAvailable,
)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_INCOMING_DIR = Path(r"C:\AshCaptions\web_uploads")
DEFAULT_SSE_POLL_INTERVAL = 1.0  # seconds; how often we re-check request.is_disconnected()
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def create_app(
    queue: JobQueue,
    catalogue: LanguageCatalogueProvider,
    *,
    style_provider: StyleProvider | None = None,
    preview_renderer: PreviewRenderer | None = None,
    update_applier: UpdateApplier | None = None,
    incoming_dir: Path | None = None,
    sse_poll_interval: float = DEFAULT_SSE_POLL_INTERVAL,
) -> FastAPI:
    """Build the FastAPI app.

    `incoming_dir` is where files uploaded through the control page are
    written before being handed to `queue.submit()`. It is deliberately a
    directory distinct from the watch folder (`C:\\AshCaptions\\in\\`) so an
    upload is never picked up a second time by the folder watcher.

    `style_provider`/`preview_renderer`/`update_applier` default to real
    implementations backed by `ash_captions.styles`/`ash_captions.engine`/
    `ash_captions.app.updater` (see `styles_adapter.py`/`preview_adapter.py`/
    `update_adapter.py`) so production callers don't need to change to get
    the style editor (spec 7A) or in-app updates (spec 11.4) working --
    tests inject fakes instead, same as `queue`/`catalogue`.

    Note `update_applier` only covers *applying* an update. *Checking* for
    one is owned by whoever calls this (`app/__main__.py` in production),
    which sets `app.state.update_state` itself, after this returns, to
    whatever `ash_captions.app.updater.check_for_update_in_background`
    populates -- this function does not touch that attribute at all, and
    the routes below treat its absence (e.g. in a test that never sets it)
    as a normal "no update" outcome, not an error.
    """
    app = FastAPI(title="ASH Captions")
    app.state.queue = queue
    app.state.catalogue = catalogue
    app.state.style_provider = style_provider or _default_style_provider()
    app.state.preview_renderer = preview_renderer or _default_preview_renderer()
    app.state.update_applier = update_applier or _default_update_applier()
    app.state.incoming_dir = incoming_dir or DEFAULT_INCOMING_DIR
    app.state.sse_poll_interval = sse_poll_interval

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

    # Serves style.css and app.js alongside the page. index.html is served
    # from "/" below (not through this mount) so the control page works at
    # the bare root URL.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/style-editor")
    async def style_editor_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "style_editor.html")

    @app.get("/api/languages", response_model=list[Language])
    async def list_languages(
        catalogue: LanguageCatalogueProvider = Depends(get_catalogue),
    ) -> list[Language]:
        return catalogue.list_languages()

    @app.get("/api/jobs", response_model=list[Job])
    async def list_jobs(queue: JobQueue = Depends(get_queue)) -> list[Job]:
        return queue.list_jobs()

    @app.post("/api/jobs/by-path", response_model=Job, status_code=201)
    async def submit_job_by_path(
        body: JobPathRequest,
        queue: JobQueue = Depends(get_queue),
        catalogue: LanguageCatalogueProvider = Depends(get_catalogue),
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> Job:
        """Primary submission route. The footage is already on this machine
        (spec §4.4), so this reads it in place -- no copy, no upload, works
        for a multi-GB 4K file exactly as fast as a small one."""
        options = _validate_options(
            catalogue,
            style_provider,
            body.language,
            body.dialect,
            body.preset,
            body.burn_in,
            body.translate_to_english,
        )
        path = _validate_local_path(body.path)
        return queue.submit(path, options)

    @app.post("/api/jobs", response_model=Job, status_code=201)
    async def submit_job(
        request: Request,
        file: UploadFile,
        language: str = Form(...),
        dialect: str | None = Form(None),
        preset: str = Form(...),
        burn_in: bool = Form(False),
        translate_to_english: bool = Form(False),
        queue: JobQueue = Depends(get_queue),
        catalogue: LanguageCatalogueProvider = Depends(get_catalogue),
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> Job:
        """Secondary submission route -- an actual byte upload. Kept for cases
        where the footage isn't reachable by a local path (e.g. a network
        share the service account can't see). Prefer /api/jobs/by-path:
        this route copies the whole file to `incoming_dir` first, which is
        slow and wastes disk for the multi-GB files editors work with."""
        options = _validate_options(
            catalogue, style_provider, language, dialect, preset, burn_in, translate_to_english
        )
        _validate_upload(file)

        # Each upload gets its own subdirectory so the on-disk filename can
        # stay the original name (the queue derives `Job.filename` from the
        # path) while still guaranteeing no collisions between uploads.
        incoming_dir: Path = request.app.state.incoming_dir
        job_dir = incoming_dir / uuid.uuid4().hex
        job_dir.mkdir(parents=True, exist_ok=True)
        dest = job_dir / _safe_filename(file.filename)

        # Stream to disk in chunks -- never hold the whole file in memory.
        # Editors routinely work with multi-GB 4K files; `await file.read()`
        # with no size arg reads the entire upload into RAM and would OOM.
        total_bytes = 0
        with dest.open("wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                out.write(chunk)
                total_bytes += len(chunk)

        if total_bytes == 0:
            dest.unlink(missing_ok=True)
            job_dir.rmdir()
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        return queue.submit(dest, options)

    @app.post("/api/jobs/{job_id}/retry", response_model=Job)
    async def retry_job(job_id: str, queue: JobQueue = Depends(get_queue)) -> Job:
        try:
            return queue.retry(job_id)
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
        except JobNotRetryableError:
            raise HTTPException(status_code=409, detail=f"Job {job_id!r} is not in a retryable state.")

    # --- Caption styling (spec 7A) ------------------------------------------

    @app.get("/api/styles", response_model=list[StyleSummary])
    async def list_styles(
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> list[StyleSummary]:
        return style_provider.list_styles()

    @app.get("/api/styles/{name}", response_model=StyleSummary)
    async def get_style(
        name: str,
        shipped_only: bool = False,
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> StyleSummary:
        try:
            return style_provider.get_style(name, shipped_only=shipped_only)
        except StyleNotFoundError:
            raise HTTPException(status_code=404, detail=f"Style {name!r} not found.")

    @app.put("/api/styles/{name}", response_model=StyleSummary)
    async def save_style(
        name: str,
        definition: dict = Body(...),
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> StyleSummary:
        try:
            return style_provider.save_style(name, definition)
        except StyleValidationFailedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/styles/{name}", status_code=204)
    async def delete_style(
        name: str,
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> Response:
        try:
            style_provider.delete_style(name)
        except StyleIsShippedError:
            raise HTTPException(
                status_code=409, detail=f"{name!r} is a built-in style and can't be deleted."
            )
        except StyleNotFoundError:
            raise HTTPException(status_code=404, detail=f"Style {name!r} not found.")
        return Response(status_code=204)

    @app.get("/api/fonts", response_model=list[str])
    async def list_fonts(style_provider: StyleProvider = Depends(get_style_provider)) -> list[str]:
        return style_provider.list_fonts()

    @app.post("/api/styles/preview", response_model=PreviewJob, status_code=202)
    async def submit_preview(
        body: PreviewRequest,
        preview_renderer: PreviewRenderer = Depends(get_preview_renderer),
    ) -> PreviewJob:
        """Kicks off a ~3s styled preview render (spec 7A.3) and returns a
        job handle immediately -- rendering takes real seconds, so the
        browser polls GET /api/styles/preview/{id} rather than this route
        blocking."""
        video_path = _validate_local_path(body.video_path)
        try:
            return preview_renderer.submit_preview(video_path, body.start_seconds, body.style)
        except StyleValidationFailedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/styles/preview/{job_id}", response_model=PreviewJob)
    async def get_preview(
        job_id: str,
        preview_renderer: PreviewRenderer = Depends(get_preview_renderer),
    ) -> PreviewJob:
        try:
            return preview_renderer.get_preview(job_id)
        except PreviewNotFoundError:
            raise HTTPException(status_code=404, detail=f"Preview job {job_id!r} not found.")

    @app.get("/api/styles/preview/{job_id}/clip")
    async def get_preview_clip(
        job_id: str,
        preview_renderer: PreviewRenderer = Depends(get_preview_renderer),
    ) -> FileResponse:
        try:
            job = preview_renderer.get_preview(job_id)
        except PreviewNotFoundError:
            raise HTTPException(status_code=404, detail=f"Preview job {job_id!r} not found.")
        if job.status != PreviewStatus.DONE or not job.clip_path:
            raise HTTPException(status_code=409, detail=f"Preview job {job_id!r} isn't ready yet.")
        return FileResponse(job.clip_path, media_type="video/mp4")

    # --- In-app updates (spec 11.4) -----------------------------------------

    @app.get("/api/update", response_model=UpdateAvailable | None)
    async def get_update(request: Request, queue: JobQueue = Depends(get_queue)) -> UpdateAvailable | None:
        info = _current_update_info(request)
        if info is None:
            return None
        return UpdateAvailable(
            version=info.version,
            notes=info.notes,
            size_bytes=info.size_bytes,
            blocked_reason=_update_blocked_reason(queue),
        )

    @app.post("/api/update/apply", response_model=UpdateApplyJob, status_code=202)
    async def submit_update_apply(
        request: Request,
        queue: JobQueue = Depends(get_queue),
        update_applier: UpdateApplier = Depends(get_update_applier),
    ) -> UpdateApplyJob:
        """The click IS the consent -- no confirmation dialog here or in the
        frontend (a second "are you sure?" just trains people to click
        through unread). Applying restarts the app; the control page says
        so beside the button, not this route."""
        info = _current_update_info(request)
        if info is None:
            raise HTTPException(status_code=404, detail="No update is currently available.")

        blocked_reason = _update_blocked_reason(queue)
        if blocked_reason is not None:
            raise HTTPException(status_code=409, detail=blocked_reason)

        return update_applier.submit_apply(info)

    @app.get("/api/update/apply/{job_id}", response_model=UpdateApplyJob)
    async def get_update_apply(
        job_id: str,
        update_applier: UpdateApplier = Depends(get_update_applier),
    ) -> UpdateApplyJob:
        try:
            return update_applier.get_apply_status(job_id)
        except UpdateApplyNotFoundError:
            raise HTTPException(status_code=404, detail=f"Update job {job_id!r} not found.")

    @app.get("/api/events")
    async def events(request: Request, queue: JobQueue = Depends(get_queue)) -> StreamingResponse:
        poll_interval: float = request.app.state.sse_poll_interval

        async def event_stream() -> AsyncIterator[str]:
            subscription = queue.subscribe()
            try:
                while True:
                    # CRITICAL (spec §8.3): check disconnection on every loop
                    # iteration, not just when a new event arrives -- otherwise
                    # a closed tab with no further queue activity leaves this
                    # generator running forever.
                    if await request.is_disconnected():
                        break
                    try:
                        snapshot = await asyncio.wait_for(
                            subscription.__anext__(), timeout=poll_interval
                        )
                    except asyncio.TimeoutError:
                        continue
                    except StopAsyncIteration:
                        break
                    payload = json.dumps([job.model_dump(mode="json") for job in snapshot])
                    yield f"data: {payload}\n\n"
            finally:
                aclose = getattr(subscription, "aclose", None)
                if aclose is not None:
                    await aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


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


def _current_update_info(request: Request):
    """Whatever the last background check found -- structurally an
    `app.updater.UpdateInfo`, or None for "no update" (or "nobody has
    checked yet", which looks identical and is fine to treat the same way).

    Reads `request.app.state.update_state` via getattr rather than a
    dependency-injected getter (unlike queue/catalogue/style_provider/
    preview_renderer/update_applier above) because that attribute isn't set
    by `create_app()` at all -- `app/__main__.py` sets it directly on the
    FastAPI app object it gets back, after construction (see
    `create_app()`'s own docstring on this). A test -- or any other caller
    of `create_app()` that never sets it -- gets a normal "no update"
    result here, not an AttributeError.
    """
    state = getattr(request.app.state, "update_state", None)
    if state is None:
        return None
    return state.get()


def _update_blocked_reason(queue: JobQueue) -> str | None:
    """Non-None while applying an update should be refused because a
    caption job is running (`app.updater.apply_update`'s own module-level
    guard refuses this too; checking here as well lets the control page
    disable its Update button proactively -- spec: "so the editor
    understands rather than clicks and gets rejected" -- instead of only
    finding out after a click)."""
    if any(job.status == JobStatus.RUNNING for job in queue.list_jobs()):
        return "A caption job is still running. Try again when the queue is clear."
    return None


def _validate_options(
    catalogue: LanguageCatalogueProvider,
    style_provider: StyleProvider,
    language: str,
    dialect: str | None,
    preset: str,
    burn_in: bool,
    translate_to_english: bool,
) -> JobOptions:
    languages = {lang.code: lang for lang in catalogue.list_languages()}
    lang_entry = languages.get(language)
    if lang_entry is None:
        raise HTTPException(status_code=400, detail=f"Unknown language {language!r}.")

    if dialect is not None:
        valid_dialects = {d.code for d in lang_entry.dialects}
        if dialect not in valid_dialects:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown dialect {dialect!r} for language {language!r}.",
            )

    # "preset" is a style name (spec 7A) -- the job form's dropdown lists
    # every style from GET /api/styles, not just the original CLEAN/POP
    # pair, so validate against that same live list rather than a static
    # tuple. Uppercased as a fallback (not tried first) so a shipped name
    # typed in lowercase (e.g. by an older client, or /api/jobs/by-path
    # called directly) still resolves, without mangling the exact case of
    # a mixed-case user style name coming from the dropdown.
    valid_presets = {style.name for style in style_provider.list_styles()}
    preset_normalized = preset if preset in valid_presets else preset.upper()
    if preset_normalized not in valid_presets:
        raise HTTPException(status_code=400, detail=f"Unknown preset {preset!r}.")

    return JobOptions(
        language=language,
        dialect=dialect,
        preset=preset_normalized,
        burn_in=burn_in,
        translate_to_english=translate_to_english,
    )


def _validate_upload(file: UploadFile) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix!r}. Expected a video file.",
        )


def _safe_filename(filename: str) -> str:
    """Strip any path components so a crafted filename can't escape incoming_dir."""
    return Path(filename).name


def _clean_path_string(raw: str) -> str:
    """Strip whitespace and a pair of surrounding quotes.

    Windows Explorer's "Copy as path" wraps the result in double quotes
    (`"D:\\clip.mp4"`); pasted verbatim that would otherwise fail the
    exists() check and be the #1 support question.
    """
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def _validate_local_path(raw_path: str) -> Path:
    cleaned = _clean_path_string(raw_path)
    if not cleaned:
        raise HTTPException(status_code=400, detail="No file path provided.")

    path = Path(cleaned)
    if not path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Can't find {cleaned!r}. Check the path and try again.",
        )
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"{cleaned!r} is not a file.")
    if path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {path.suffix!r}. Expected a video file.",
        )
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Can't read {cleaned!r}: {exc.strerror or exc}.")

    return path


def run_server(app: FastAPI, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve `app`. Refuses anything but 127.0.0.1 -- this tool must never be
    reachable from the LAN (spec §4.4, §5)."""
    if host != "127.0.0.1":
        raise ValueError(
            f"ASH Captions must bind to 127.0.0.1 only; refusing host {host!r}."
        )
    import uvicorn

    uvicorn.run(app, host=host, port=port)
