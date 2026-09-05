"""Foundation for the Review page (a Veed-style live caption preview over
the finished job): the data routes a player needs, plus the downloads
v0.6 §3 asked for.

* `GET /api/jobs/{id}/files` -- what the job wrote (name, size) so the
  page can offer downloads and find the caption track.
* `GET /api/jobs/{id}/files/{name}` -- one named output, served as an
  attachment (v0.6 §3: "export everywhere"). `name` is looked up in the
  same listing `/files` returns and never joined onto `output_dir` --
  a name that isn't in the listing (a traversal attempt, an absolute
  path, or just a typo) is a 404, not a file, because it was never going
  to be a match in the first place.
* `GET /api/jobs/{id}/video` -- the job's *input* footage, with HTTP Range
  support so a `<video>` tag can seek. Starlette's `FileResponse` answers
  `Range` natively (206 + `Content-Range`, 416 when unsatisfiable, always
  `Accept-Ranges: bytes`) and streams in chunks -- it never reads the whole
  file, which matters for multi-GB 4K sources.
* `GET /api/jobs/{id}/ass` -- the rendered `.ass` caption file, for an
  in-browser renderer to draw over the video.

All these depend on `Job.input_path`/`Job.output_dir`, which the queue
fills in when it can (see `models.Job`); a queue that doesn't yet gets a
plain 404 here rather than a guess at where files live.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .interfaces import JobQueue
from .models import Job

VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mxf": "application/mxf",
}

# The text outputs a download can name: captions and the transcript. Video
# extensions reuse VIDEO_MEDIA_TYPES below, so the two maps together cover
# everything a job's output folder ever holds.
DOWNLOAD_TEXT_MEDIA_TYPES = {
    ".srt": "text/plain; charset=utf-8",
    ".ass": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


class OutputFile(BaseModel):
    name: str
    size_bytes: int


def build_review_router(get_queue: Callable[[Request], JobQueue]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs/{job_id}/files", response_model=list[OutputFile])
    async def list_output_files(job_id: str, queue: JobQueue = Depends(get_queue)) -> list[OutputFile]:
        output_dir = output_dir_of(job_or_404(queue, job_id))
        return await run_in_threadpool(_scan_outputs, output_dir)

    @router.get("/api/jobs/{job_id}/files/{name}")
    async def download_output_file(job_id: str, name: str, queue: JobQueue = Depends(get_queue)) -> FileResponse:
        output_dir = output_dir_of(job_or_404(queue, job_id))
        outputs = await run_in_threadpool(_scan_outputs, output_dir)
        match = next((entry for entry in outputs if entry.name == name), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f"{name!r} is not one of job {job_id!r}'s output files.")
        path = output_dir / match.name
        media_type = _download_media_type(path)
        return FileResponse(path, media_type=media_type, filename=path.name)

    @router.get("/api/jobs/{job_id}/video")
    async def stream_input_video(job_id: str, queue: JobQueue = Depends(get_queue)) -> FileResponse:
        job = job_or_404(queue, job_id)
        if not job.input_path:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no input file on record.")
        path = Path(job.input_path)
        if not await run_in_threadpool(path.is_file):
            raise HTTPException(status_code=404, detail=f"The input file for job {job_id!r} is no longer there.")
        media_type = VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")

    @router.get("/api/jobs/{job_id}/ass")
    async def serve_ass(job_id: str, queue: JobQueue = Depends(get_queue)) -> FileResponse:
        output_dir = output_dir_of(job_or_404(queue, job_id))
        candidates = await run_in_threadpool(lambda: sorted(output_dir.glob("*.ass")))
        if not candidates:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no .ass caption file yet.")
        return FileResponse(candidates[0], media_type="text/plain; charset=utf-8", filename=candidates[0].name)

    return router


def job_or_404(queue: JobQueue, job_id: str) -> Job:
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
    return job


def output_dir_of(job: Job) -> Path:
    if not job.output_dir:
        raise HTTPException(status_code=404, detail=f"Job {job.id!r} has no output folder on record.")
    return Path(job.output_dir)


def _download_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return VIDEO_MEDIA_TYPES.get(suffix) or DOWNLOAD_TEXT_MEDIA_TYPES.get(suffix) or "application/octet-stream"


def _scan_outputs(output_dir: Path) -> list[OutputFile]:
    if not output_dir.is_dir():
        return []
    return [
        OutputFile(name=entry.name, size_bytes=entry.stat().st_size)
        for entry in sorted(output_dir.iterdir())
        if entry.is_file()
    ]
