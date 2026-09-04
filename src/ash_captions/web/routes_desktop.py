"""Desktop and housekeeping routes -- the control page's "Browse...", the
job cards' thumbnails, and the "Remove from list" / "Open folder" actions.

* ``POST /api/pick-file`` -- opens the native Open File dialog on the
  editor's desktop and answers ``{"path": ...}`` (null when cancelled).
  409 while a dialog is already open. Blocks for as long as the person
  browses, so it runs in the threadpool like every other slow call.
* ``GET /api/jobs/{id}/thumb`` -- a 320-px JPEG of the footage, generated
  once into the job's output folder (``thumbs.py``) and served with a
  day's caching. Falls back to the burned output when the source is gone;
  404 with neither.
* ``DELETE /api/jobs/{id}`` -- forget the row. Files stay on disk. 409
  while the job is pending or running, 501 on a queue without
  ``remove_job``.
* ``POST /api/jobs/{id}/reveal`` -- open Explorer on the job's output:
  the burned video selected when there is one, else the folder. Only the
  job's own ``output_dir`` is ever revealed; no path comes from the
  request.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .interfaces import (
    FilePicker,
    JobNotFoundError,
    JobNotRemovableError,
    JobQueue,
    PathRevealer,
    PickerBusyError,
)
from .routes_review import job_or_404, output_dir_of
from .routes_studio import _burned_output
from .thumbs import ensure_thumbnail

CANNOT_REMOVE_DETAIL = "this build cannot remove jobs"
PICKER_BUSY_DETAIL = "A file dialog is already open. Finish with it first."
THUMB_CACHE_CONTROL = "private, max-age=86400"


class PickedFile(BaseModel):
    path: str | None


def build_desktop_router(
    get_queue: Callable[[Request], JobQueue],
    get_file_picker: Callable[[Request], FilePicker],
    get_revealer: Callable[[Request], PathRevealer],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/pick-file", response_model=PickedFile)
    async def pick_file(picker: FilePicker = Depends(get_file_picker)) -> PickedFile:
        try:
            chosen = await run_in_threadpool(picker.pick_video)
        except PickerBusyError:
            raise HTTPException(status_code=409, detail=PICKER_BUSY_DETAIL)
        return PickedFile(path=chosen or None)

    @router.get("/api/jobs/{job_id}/thumb")
    async def job_thumbnail(job_id: str, queue: JobQueue = Depends(get_queue)) -> FileResponse:
        job = job_or_404(queue, job_id)
        output_dir = output_dir_of(job)
        thumb = await run_in_threadpool(_thumb_for, output_dir, job.input_path)
        if thumb is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no thumbnail.")
        return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": THUMB_CACHE_CONTROL})

    @router.delete("/api/jobs/{job_id}", status_code=204)
    async def remove_job(job_id: str, queue: JobQueue = Depends(get_queue)) -> Response:
        remover = getattr(queue, "remove_job", None)
        if not callable(remover):
            raise HTTPException(status_code=501, detail=CANNOT_REMOVE_DETAIL)
        try:
            await run_in_threadpool(remover, job_id)
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
        except JobNotRemovableError:
            raise HTTPException(
                status_code=409, detail=f"Job {job_id!r} is still in the queue; wait for it to finish first."
            )
        return Response(status_code=204)

    @router.post("/api/jobs/{job_id}/reveal", status_code=204)
    async def reveal_job(
        job_id: str,
        queue: JobQueue = Depends(get_queue),
        revealer: PathRevealer = Depends(get_revealer),
    ) -> Response:
        output_dir = output_dir_of(job_or_404(queue, job_id))
        target = await run_in_threadpool(_reveal_target, output_dir)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no output folder yet.")
        try:
            await run_in_threadpool(revealer.reveal, target)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r}'s output folder is gone.")
        return Response(status_code=204)

    return router


def _thumb_for(output_dir: Path, input_path: str | None) -> Path | None:
    """Threadpool: the source footage first, the burned output as the
    fallback when the source has been moved or cleaned up."""
    return ensure_thumbnail(output_dir, input_path, _burned_output(output_dir))


def _reveal_target(output_dir: Path) -> Path | None:
    if not output_dir.is_dir():
        return None
    return _burned_output(output_dir) or output_dir
