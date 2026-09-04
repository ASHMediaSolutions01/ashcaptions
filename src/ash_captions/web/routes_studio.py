"""Studio page data routes (the Veed-style "play it, pick a look, burn it"
review page served at /studio/{job_id} from static/studio.html).

* `GET /api/jobs/{id}` -- one job. The list route is capped at the newest
  100, which an older job can fall off; the page needs exactly one anyway.
* `POST /api/jobs/{id}/restyle {"preset", "caption_x"?, "caption_y"?}` --
  regenerate the job's `.ass` in another look from its saved word timings
  (no transcription, < 1 s), optionally at a dragged caption position
  (fractions of the frame, both or neither; omitted keeps, nulls clear).
  The page then reloads the track in the browser renderer, keeping the
  playhead. Real work, so it runs in the threadpool like everything else.
* `POST /api/jobs/{id}/burn {"preset"}` -- enqueue a burn-only job for the
  same footage in that look; the editor watches it in the queue.
* `GET /api/jobs/{id}/srt` -- the transcript cards, for the read-only strip.
* `GET /api/jobs/{id}/output` -- the burned `.captioned.mp4` with HTTP Range
  support, what the page falls back to when the original footage has since
  been deleted (a watch-folder job whose input was cleaned up).
* `GET /api/fonts/files` + `GET /api/fonts/file/{filename}` -- the bundled
  font files, so the browser renderer (and the look cards' type samples)
  draw the same faces ffmpeg will burn. Only files the font manifest lists
  are served: the filename is looked up in that list, never joined onto a
  directory, so there is no path to traverse.

`restyle`/`submit_burn` are optional on the queue (see `interfaces.JobQueue`):
a queue without them answers 501, a missing job 404, and a `ValueError`
from the queue (an older job with no saved words, an unknown preset) 409
carrying the queue's own message.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .interfaces import BundledFontFile, JobNotFoundError, JobQueue, StyleProvider
from .models import FontFile, Job, PresetRequest
from .routes_review import VIDEO_MEDIA_TYPES, job_or_404, output_dir_of

FONT_MEDIA_TYPES = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
CANNOT_RESTYLE_DETAIL = "this build cannot restyle"
CANNOT_BURN_DETAIL = "this build cannot burn from Studio"


def build_studio_router(
    get_queue: Callable[[Request], JobQueue],
    get_style_provider: Callable[[Request], StyleProvider],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs/{job_id}", response_model=Job)
    async def get_job(job_id: str, queue: JobQueue = Depends(get_queue)) -> Job:
        return job_or_404(queue, job_id)

    @router.post("/api/jobs/{job_id}/restyle", response_model=Job)
    async def restyle_job(job_id: str, body: PresetRequest, queue: JobQueue = Depends(get_queue)) -> Job:
        # Keys omitted: keep the job's position (no keyword at all, so an
        # older queue's restyle(job_id, preset) still works); both null: clear.
        extra = {"position": body.caption_position} if body.position_sent else {}
        return await _call_optional(queue, "restyle", job_id, body.preset, missing=CANNOT_RESTYLE_DETAIL, **extra)

    @router.post("/api/jobs/{job_id}/burn", response_model=Job, status_code=201)
    async def burn_job(job_id: str, body: PresetRequest, queue: JobQueue = Depends(get_queue)) -> Job:
        return await _call_optional(queue, "submit_burn", job_id, body.preset, missing=CANNOT_BURN_DETAIL)

    @router.get("/api/jobs/{job_id}/srt")
    async def serve_srt(job_id: str, queue: JobQueue = Depends(get_queue)) -> FileResponse:
        output_dir = output_dir_of(job_or_404(queue, job_id))
        path = await run_in_threadpool(_transcript_srt, output_dir)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no .srt transcript yet.")
        return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)

    @router.get("/api/jobs/{job_id}/output")
    async def stream_burned_output(job_id: str, queue: JobQueue = Depends(get_queue)) -> FileResponse:
        output_dir = output_dir_of(job_or_404(queue, job_id))
        path = await run_in_threadpool(_burned_output, output_dir)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no burned-in video.")
        media_type = VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")

    @router.get("/api/fonts/files", response_model=list[FontFile])
    async def list_font_files(style_provider: StyleProvider = Depends(get_style_provider)) -> list[FontFile]:
        """Only faces whose file is actually installed: a missing .ttf
        would otherwise become a failed @font-face fetch on every visit."""
        entries = await run_in_threadpool(_installed_font_files, style_provider)
        return [FontFile(family=entry.family, url=f"/api/fonts/file/{quote(entry.path.name)}") for entry in entries]

    @router.get("/api/fonts/file/{filename}")
    async def serve_font_file(
        filename: str, style_provider: StyleProvider = Depends(get_style_provider)
    ) -> FileResponse:
        entry = next((e for e in _bundled_font_files(style_provider) if e.path.name == filename), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{filename!r} is not a bundled font.")
        if not await run_in_threadpool(entry.path.is_file):
            raise HTTPException(status_code=404, detail=f"The font file {filename!r} is not installed.")
        media_type = FONT_MEDIA_TYPES.get(entry.path.suffix.lower(), "application/octet-stream")
        return FileResponse(
            entry.path, media_type=media_type, filename=entry.path.name, content_disposition_type="inline"
        )

    return router


async def _call_optional(
    queue: JobQueue, method_name: str, job_id: str, preset: str, *, missing: str, **extra: Any
) -> Any:
    method = getattr(queue, method_name, None)
    if not callable(method):
        raise HTTPException(status_code=501, detail=missing)
    try:
        return await run_in_threadpool(method, job_id, preset, **extra)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc) or f"Job {job_id!r} can't use that look.")


def _first_match(output_dir: Path, pattern: str) -> Path | None:
    if not output_dir.is_dir():
        return None
    matches = sorted(output_dir.glob(pattern))
    return matches[0] if matches else None


def _transcript_srt(output_dir: Path) -> Path | None:
    """The source-language captions, not the English translation: a
    translate job writes both `<stem>.srt` and `<stem>.en.srt`, and a plain
    glob sorts `.en.srt` first, which put the English cards on the strip
    for a Spanish video."""
    if not output_dir.is_dir():
        return None
    candidates = sorted(output_dir.glob("*.srt"))
    primary = [p for p in candidates if not p.name.lower().endswith(".en.srt")]
    chosen = primary or candidates
    return chosen[0] if chosen else None


def _burned_output(output_dir: Path) -> Path | None:
    """The pipeline names it `<stem>.captioned.mp4`; any other .mp4 in the
    output folder is accepted as a fallback rather than guessing further."""
    return _first_match(output_dir, "*.captioned.mp4") or _first_match(output_dir, "*.mp4")


def _bundled_font_files(style_provider: StyleProvider) -> list[BundledFontFile]:
    lister = getattr(style_provider, "list_font_files", None)
    if not callable(lister):
        return []
    return list(lister())


def _installed_font_files(style_provider: StyleProvider) -> list[BundledFontFile]:
    return [entry for entry in _bundled_font_files(style_provider) if entry.path.is_file()]
