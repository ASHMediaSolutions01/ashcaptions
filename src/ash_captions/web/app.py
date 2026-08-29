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

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi import Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .interfaces import JobNotFoundError, JobNotRetryableError, JobQueue, LanguageCatalogueProvider
from .models import (
    ALLOWED_PRESETS,
    ALLOWED_VIDEO_EXTENSIONS,
    Job,
    JobOptions,
    JobPathRequest,
    Language,
)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_INCOMING_DIR = Path(r"C:\AshCaptions\web_uploads")
DEFAULT_SSE_POLL_INTERVAL = 1.0  # seconds; how often we re-check request.is_disconnected()
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def create_app(
    queue: JobQueue,
    catalogue: LanguageCatalogueProvider,
    *,
    incoming_dir: Path | None = None,
    sse_poll_interval: float = DEFAULT_SSE_POLL_INTERVAL,
) -> FastAPI:
    """Build the FastAPI app.

    `incoming_dir` is where files uploaded through the control page are
    written before being handed to `queue.submit()`. It is deliberately a
    directory distinct from the watch folder (`C:\\AshCaptions\\in\\`) so an
    upload is never picked up a second time by the folder watcher.
    """
    app = FastAPI(title="ASH Captions")
    app.state.queue = queue
    app.state.catalogue = catalogue
    app.state.incoming_dir = incoming_dir or DEFAULT_INCOMING_DIR
    app.state.sse_poll_interval = sse_poll_interval

    def get_queue(request: Request) -> JobQueue:
        return request.app.state.queue

    def get_catalogue(request: Request) -> LanguageCatalogueProvider:
        return request.app.state.catalogue

    # Serves style.css and app.js alongside the page. index.html is served
    # from "/" below (not through this mount) so the control page works at
    # the bare root URL.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

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
    ) -> Job:
        """Primary submission route. The footage is already on this machine
        (spec §4.4), so this reads it in place -- no copy, no upload, works
        for a multi-GB 4K file exactly as fast as a small one."""
        options = _validate_options(
            catalogue, body.language, body.dialect, body.preset, body.burn_in, body.translate_to_english
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
    ) -> Job:
        """Secondary submission route -- an actual byte upload. Kept for cases
        where the footage isn't reachable by a local path (e.g. a network
        share the service account can't see). Prefer /api/jobs/by-path:
        this route copies the whole file to `incoming_dir` first, which is
        slow and wastes disk for the multi-GB files editors work with."""
        options = _validate_options(catalogue, language, dialect, preset, burn_in, translate_to_english)
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


def _validate_options(
    catalogue: LanguageCatalogueProvider,
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

    preset_upper = preset.upper()
    if preset_upper not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset {preset!r}.")

    return JobOptions(
        language=language,
        dialect=dialect,
        preset=preset_upper,
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
