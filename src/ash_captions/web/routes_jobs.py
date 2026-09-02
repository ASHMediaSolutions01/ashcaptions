"""Job and language routes: list, submit (by path or upload), retry. Split
out of app.py so that module stays a slim application factory --
`create_app()` builds this router with its dependency getters and mounts
it, same as `routes_styles.py`/`routes_updates.py`.

Nothing here may block the event loop: the SSE stream and every other
request share it, and an hour-long job is exactly when an editor is
staring at the page. So filesystem work -- validating a pasted path (which
can stall on an SMB share), copying an upload to disk -- runs in Starlette's
threadpool.
"""
from __future__ import annotations

import inspect
import shutil
import uuid
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from .interfaces import (
    JobNotFoundError,
    JobNotRetryableError,
    JobQueue,
    LanguageCatalogueProvider,
    StyleProvider,
)
from .models import ALLOWED_VIDEO_EXTENSIONS, Job, JobOptions, JobPathRequest, Language
from .validation import validate_local_path

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB
# Hard ceiling for the upload route. Editors' real footage is routinely
# bigger -- that's what /api/jobs/by-path is for, which reads in place.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
UPLOAD_TOO_LARGE_DETAIL = (
    "That file is over 2 GB -- too big to upload a copy. Paste its location into the "
    "\"Video file location\" field instead; the app reads it in place with no copy."
)
# Newest-first cap on what the page and every SSE frame carry. A studio
# that has run thousands of jobs must not pay for all of them on every
# progress tick.
JOB_LIST_LIMIT = 100


def list_jobs_for_web(queue: JobQueue, limit: int = JOB_LIST_LIMIT) -> list[Job]:
    """`queue.list_jobs()` capped to the newest `limit`. Passes `limit=`
    down when the implementation accepts it (so the database does the
    capping) and truncates the result otherwise."""
    try:
        accepts_limit = "limit" in inspect.signature(queue.list_jobs).parameters
    except (TypeError, ValueError):
        accepts_limit = False
    jobs = queue.list_jobs(limit=limit) if accepts_limit else queue.list_jobs()
    return list(jobs)[:limit]


def build_jobs_router(
    get_queue: Callable[[Request], JobQueue],
    get_catalogue: Callable[[Request], LanguageCatalogueProvider],
    get_style_provider: Callable[[Request], StyleProvider],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/languages", response_model=list[Language])
    async def list_languages(
        catalogue: LanguageCatalogueProvider = Depends(get_catalogue),
    ) -> list[Language]:
        return catalogue.list_languages()

    @router.get("/api/jobs", response_model=list[Job])
    async def list_jobs(queue: JobQueue = Depends(get_queue)) -> list[Job]:
        return list_jobs_for_web(queue)

    @router.post("/api/jobs/by-path", response_model=Job, status_code=201)
    async def submit_job_by_path(
        body: JobPathRequest,
        queue: JobQueue = Depends(get_queue),
        catalogue: LanguageCatalogueProvider = Depends(get_catalogue),
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> Job:
        """Primary submission route. The footage is already on this machine
        (spec §4.4), so this reads it in place -- no copy, no upload, works
        for a multi-GB 4K file exactly as fast as a small one."""
        options = validate_options(
            catalogue,
            style_provider,
            body.language,
            body.dialect,
            body.preset,
            body.burn_in,
            body.translate_to_english,
        )
        path = await run_in_threadpool(validate_local_path, body.path)
        return queue.submit(path, options)

    @router.post("/api/jobs", response_model=Job, status_code=201)
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
        _reject_oversized(request)
        options = validate_options(
            catalogue, style_provider, language, dialect, preset, burn_in, translate_to_english
        )
        _validate_upload(file)

        # Each upload gets its own subdirectory so the on-disk filename can
        # stay the original name (the queue derives `Job.filename` from the
        # path) while still guaranteeing no collisions between uploads.
        incoming_dir: Path = request.app.state.incoming_dir
        job_dir = incoming_dir / uuid.uuid4().hex
        dest = job_dir / _safe_filename(file.filename)

        try:
            total_bytes = await run_in_threadpool(_copy_upload_to_disk, file, job_dir, dest)
        except OSError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(
                status_code=500,
                detail=f"Couldn't save the upload to {incoming_dir}: {exc.strerror or exc}.",
            )

        if total_bytes == 0:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        return queue.submit(dest, options)

    @router.post("/api/jobs/{job_id}/retry", response_model=Job)
    async def retry_job(job_id: str, queue: JobQueue = Depends(get_queue)) -> Job:
        try:
            return queue.retry(job_id)
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
        except JobNotRetryableError:
            raise HTTPException(status_code=409, detail=f"Job {job_id!r} is not in a retryable state.")

    return router


def validate_options(
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


def _reject_oversized(request: Request) -> None:
    """Checked against Content-Length before anything is validated or
    written. Starlette has already spooled the multipart body by the time
    this route runs, so this can't stop the bytes arriving -- but it does
    stop a second copy being written and the job being enqueued."""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE_DETAIL)


def _copy_upload_to_disk(file: UploadFile, job_dir: Path, dest: Path) -> int:
    """Runs in the threadpool. Streams the spooled upload to `dest` in
    chunks -- never `read()` with no size; editors upload multi-GB files."""
    job_dir.mkdir(parents=True, exist_ok=True)
    source = file.file
    source.seek(0)
    total = 0
    with dest.open("wb") as out:
        while chunk := source.read(UPLOAD_CHUNK_SIZE):
            out.write(chunk)
            total += len(chunk)
    return total


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
