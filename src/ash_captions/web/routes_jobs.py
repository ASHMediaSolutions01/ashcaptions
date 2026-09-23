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
from .validation import validate_client_name, validate_local_path

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


def list_jobs_for_web(
    queue: JobQueue, limit: int = JOB_LIST_LIMIT, *, query: str | None = None, offset: int = 0
) -> list[Job]:
    """`queue.list_jobs()` capped to the newest `limit`, optionally searched
    and paged. Each keyword is passed down only when the implementation
    accepts it (so the database does the work) and applied here otherwise,
    so an older queue keeps answering."""
    try:
        params = inspect.signature(queue.list_jobs).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {}
    if "limit" in params:
        kwargs["limit"] = limit
    if query and "query" in params:
        kwargs["query"] = query
    if offset and "offset" in params:
        kwargs["offset"] = offset
    jobs = queue.list_jobs(**kwargs)
    if query and "query" not in params:
        needle = query.lower()
        jobs = [j for j in jobs if needle in _searchable(j)]
    if offset and "offset" not in params:
        jobs = jobs[offset:]
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
    async def list_jobs(
        q: str | None = None,
        offset: int = 0,
        limit: int = JOB_LIST_LIMIT,
        queue: JobQueue = Depends(get_queue),
    ) -> list[Job]:
        """The newest jobs; with `q` a search over the file name (and the
        client folder it came from), with `offset` the next page of it."""
        if offset < 0 or limit < 1 or limit > JOB_LIST_LIMIT:
            raise HTTPException(status_code=400, detail=f"offset must be 0 or more and limit 1-{JOB_LIST_LIMIT}.")
        return list_jobs_for_web(queue, limit, query=(q or "").strip()[:200] or None, offset=offset)

    @router.post("/api/jobs/by-path", response_model=Job, status_code=201)
    async def submit_job_by_path(
        body: JobPathRequest,
        request: Request,
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
            client=body.client,
            behind_speaker=body.behind_speaker,
            reframe=body.reframe,
            speaker_labels=body.speaker_labels,
            default_preset=_default_preset(request),
        )
        path = await run_in_threadpool(validate_local_path, body.path)
        return queue.submit(path, options)

    @router.post("/api/jobs", response_model=Job, status_code=201)
    async def submit_job(
        request: Request,
        file: UploadFile,
        language: str = Form(...),
        dialect: str | None = Form(None),
        preset: str | None = Form(None),
        burn_in: bool = Form(False),
        translate_to_english: bool = Form(False),
        client: str | None = Form(None),
        behind_speaker: bool = Form(False),
        reframe: bool = Form(False),
        speaker_labels: bool = Form(False),
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
            catalogue, style_provider, language, dialect, preset, burn_in, translate_to_english,
            client=client, behind_speaker=behind_speaker, reframe=reframe,
            speaker_labels=speaker_labels, default_preset=_default_preset(request),
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
        except JobNotRetryableError as exc:
            # The adapter raises this with the job id for a plain state
            # refusal, and with the store's own sentence when the same file
            # is already queued -- which is the one an editor can act on.
            why = str(exc)
            detail = why if why and why != job_id else f"Job {job_id!r} is not in a retryable state."
            raise HTTPException(status_code=409, detail=detail)

    return router


def _searchable(job: Job) -> str:
    client = job.options.client if job.options and job.options.client else ""
    return f"{job.input_path or job.filename or ''} {client}".lower()


def _default_preset(request: Request) -> str | None:
    return getattr(request.app.state, "default_preset", None)


def validate_options(
    catalogue: LanguageCatalogueProvider,
    style_provider: StyleProvider,
    language: str,
    dialect: str | None,
    preset: str | None,
    burn_in: bool,
    translate_to_english: bool,
    client: str | None = None,
    behind_speaker: bool = False,
    reframe: bool = False,
    speaker_labels: bool = False,
    default_preset: str | None = None,
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
    styles = list(style_provider.list_styles())
    valid_presets = {style.name for style in styles}
    if not preset:
        # No look chosen: the app's default, else the first in the library.
        # The editor picks the real one in the Studio.
        if default_preset and (default_preset in valid_presets or default_preset.upper() in valid_presets):
            preset = default_preset
        elif styles:
            preset = styles[0].name
        else:
            raise HTTPException(status_code=400, detail="No caption styles are installed.")
    preset_normalized = preset if preset in valid_presets else preset.upper()
    if preset_normalized not in valid_presets:
        raise HTTPException(status_code=400, detail=f"Unknown preset {preset!r}.")

    return JobOptions(
        language=language,
        dialect=dialect,
        preset=preset_normalized,
        burn_in=burn_in,
        translate_to_english=translate_to_english,
        client=validate_client_name(client),
        behind_speaker=bool(behind_speaker),
        reframe=bool(reframe),
        speaker_labels=bool(speaker_labels),
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
    chunks -- never `read()` with no size; editors upload multi-GB files.

    The byte ceiling is enforced here as well as against Content-Length:
    a chunked or dishonest request has no usable length header, and
    without this check the copy would run to whatever size arrived. On
    overflow the partial file and its job directory are removed before
    the 413 is raised, so nothing is left behind on disk."""
    job_dir.mkdir(parents=True, exist_ok=True)
    source = file.file
    source.seek(0)
    total = 0
    try:
        with dest.open("wb") as out:
            while chunk := source.read(UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE_DETAIL)
                out.write(chunk)
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
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
