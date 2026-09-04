"""Caption check routes (v0.5 spec section 2): the saved transcript, read
back for the Studio's two-line transcript panel, and the translate-only job
that adds English to it.

* `GET /api/jobs/{id}/transcript` -- `{language, words:[{w,s,e,p}],
  en_words:[{w,s,e}] | null}` from `<stem>.transcript.json` beside the
  outputs. `p` is the transcriber's per-word confidence the panel
  underlines below 0.5 (amber) and 0.3 (red); nothing is recomputed here.
  404 without a saved transcript (a job from before the Studio existed),
  409 when the file is there but unreadable.
* `POST /api/jobs/{id}/translate` -- enqueue a translate-only job for the
  same footage (never re-transcribes; writes `.en.srt` and `en_words`).
  Optional on the queue like `restyle`/`submit_burn`: 501 without it, 404
  for a missing job, 409 with the queue's own message (no transcript, the
  input file is gone). Mutating, so it needs the `X-ASH-Client` header
  like every other POST (`security.py`).

The transcript file's format belongs to `ash_captions.app.transcript`; it
is imported lazily inside `read_transcript`, the way the adapters reach
into other packages, so importing the web package never imports `app`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .interfaces import JobNotFoundError, JobQueue
from .models import Job
from .routes_review import job_or_404, output_dir_of

TRANSCRIPT_SUFFIX = ".transcript.json"
CANNOT_TRANSLATE_DETAIL = "this build cannot translate from Studio"


class TranscriptWord(BaseModel):
    """One source word: text, start, end (seconds), confidence 0..1."""

    w: str
    s: float
    e: float
    p: float


class EnglishWord(BaseModel):
    """One translated word; the English pass carries no usable confidence."""

    w: str
    s: float
    e: float


class Transcript(BaseModel):
    language: str
    words: list[TranscriptWord]
    en_words: list[EnglishWord] | None = None


def build_transcript_router(get_queue: Callable[[Request], JobQueue]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs/{job_id}/transcript", response_model=Transcript)
    async def get_transcript(job_id: str, queue: JobQueue = Depends(get_queue)) -> Transcript:
        job = job_or_404(queue, job_id)
        output_dir = output_dir_of(job)
        stem = Path(job.input_path).stem if job.input_path else None
        try:
            transcript = await run_in_threadpool(read_transcript, output_dir, stem)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if transcript is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no saved transcript.")
        return transcript

    @router.post("/api/jobs/{job_id}/translate", response_model=Job, status_code=201)
    async def translate_job(job_id: str, queue: JobQueue = Depends(get_queue)) -> Job:
        method = getattr(queue, "submit_translate", None)
        if not callable(method):
            raise HTTPException(status_code=501, detail=CANNOT_TRANSLATE_DETAIL)
        try:
            return await run_in_threadpool(method, job_id)
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc) or f"Job {job_id!r} can't be translated.")

    return router


def transcript_file(output_dir: Path, stem: str | None) -> Path | None:
    """`<stem>.transcript.json` when the job's input stem is known and the
    file exists; otherwise the first transcript in the folder; else None."""
    if not output_dir.is_dir():
        return None
    if stem is not None:
        preferred = output_dir / f"{stem}{TRANSCRIPT_SUFFIX}"
        if preferred.is_file():
            return preferred
    candidates = sorted(output_dir.glob(f"*{TRANSCRIPT_SUFFIX}"))
    return candidates[0] if candidates else None


def read_transcript(output_dir: Path, stem: str | None) -> Transcript | None:
    """None when there is no transcript file; ValueError (the loader's own
    message, naming the file) when there is one that cannot be read."""
    path = transcript_file(output_dir, stem)
    if path is None:
        return None
    from ash_captions.app.transcript import TranscriptError, load_transcript

    try:
        record = load_transcript(path)
    except TranscriptError as exc:
        raise ValueError(str(exc)) from exc
    return Transcript(
        language=record.language,
        words=[TranscriptWord(w=w.text, s=w.start, e=w.end, p=w.probability) for w in record.words],
        en_words=(
            [EnglishWord(w=w.text, s=w.start, e=w.end) for w in record.en_words]
            if record.en_words is not None
            else None
        ),
    )
