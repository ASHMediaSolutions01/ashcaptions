"""The saved transcript: read back for the Studio's transcript panel,
edited in place by the editor, and the translate-only job that adds English
to it.

* `GET /api/jobs/{id}/transcript` -- `{language, revision, en_stale,
  words:[{w,s,e,p}], meta:[...] | null, en_words:[{w,s,e}] | null}` from
  `<stem>.transcript.json` beside the outputs. `p` is the transcriber's
  per-word confidence the panel underlines below 0.5 (amber) and 0.3
  (red); nothing is recomputed here. `meta` is the parallel per-word
  editing state (v0.6): `null` until something has been edited, and
  exactly as long as `words` after that -- a parallel list rather than
  keys on each word, because that is the shape the record itself has.
  404 without a saved transcript (a job from before the Studio existed),
  409 when the file is there but unreadable.
* `PATCH /api/jobs/{id}/transcript` -- `{"revision": n, "ops": [...]}`
  (v0.6 section 1). The ops -- `set_text`, `retime`, `split`, `merge`,
  `set_style` -- are applied to one in-memory record and saved once, so a
  bad op anywhere means nothing is written. A `revision` that is not the
  record's is a 409 carrying the current transcript, so a second tab
  reloads instead of clobbering -- the same class of bug that bit two-tab
  restyling in v0.5. On success the `.ass`, `.srt` and `.txt` are
  re-rendered from the edited record and the whole new transcript comes
  back, plus the card count.
* `POST /api/jobs/{id}/glossary` -- `{"from": ..., "to": ...}` adds the
  correction to the job's client glossary (the shared one when the job
  has no client), so the *next* job gets it right at transcription time.
  Validated by the same `validate_glossary_text` the clients page uses.
* `POST /api/jobs/{id}/translate` -- enqueue a translate-only job for the
  same footage (never re-transcribes; writes `.en.srt` and `en_words`).
  Optional on the queue like `restyle`/`submit_burn`: 501 without it, 404
  for a missing job, 409 with the queue's own message (no transcript, the
  input file is gone). Mutating, so it needs the `X-ASH-Client` header
  like every other POST (`security.py`).

The transcript file's format and the operations over it belong to
`ash_captions.app.transcript`; both are imported lazily inside the
functions that need them, the way the adapters reach into other packages,
so importing the web package never imports `app`. The glossary provider is
read off `request.app.state.glossary_provider` here rather than being
passed into `build_transcript_router`, so adding these routes needed no
change to `app.py` at all.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .interfaces import ClientGlossaryProvider, GlossaryValidationFailedError, JobNotFoundError, JobQueue
from .models import Job
from .routes_clients import _problems_detail
from .routes_review import job_or_404, output_dir_of
from .validation import validate_client_name

TRANSCRIPT_SUFFIX = ".transcript.json"
CANNOT_TRANSLATE_DETAIL = "this build cannot translate from Studio"
NO_SHARED_GLOSSARY_DETAIL = "this build cannot add to the shared glossary"
STALE_REVISION_DETAIL = (
    "Somebody else changed this transcript while you were editing it. "
    "The version on screen has been refreshed; make your change again."
)


class TranscriptWord(BaseModel):
    """One source word: text, start, end (seconds), confidence 0..1."""

    w: str
    s: float
    e: float
    p: float


class WordMetaOut(BaseModel):
    """What has been done to the word at the same position by hand."""

    edited: bool = False
    retimed: bool = False
    break_before: bool = False
    no_break_before: bool = False
    style: dict[str, Any] | None = None


class EnglishWord(BaseModel):
    """One translated word; the English pass carries no usable confidence."""

    w: str
    s: float
    e: float


class Transcript(BaseModel):
    language: str
    words: list[TranscriptWord]
    en_words: list[EnglishWord] | None = None
    revision: int = 0
    # None until something has been edited; otherwise as long as `words`.
    meta: list[WordMetaOut] | None = None
    en_stale: bool = False


class TranscriptUpdate(Transcript):
    """What a PATCH answers: the whole new transcript, plus how many
    caption cards the re-render produced."""

    cards: int


class TranscriptOp(BaseModel):
    """One edit. `index` is a position in `words`; the other fields belong
    to the op that uses them and are ignored by the rest."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["set_text", "retime", "split", "merge", "set_style"]
    index: int = Field(..., ge=0)
    text: str | None = None
    all: bool = False
    start: float | None = None
    end: float | None = None
    style: dict[str, Any] | None = None


class TranscriptPatch(BaseModel):
    revision: int = Field(..., ge=0)
    ops: list[TranscriptOp] = Field(..., min_length=1, max_length=500)


class GlossaryFix(BaseModel):
    """`{"from": "haramienta", "to": "herramienta"}` -- one correction."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(..., alias="from", min_length=1, max_length=200)
    to: str = Field(..., min_length=1, max_length=200)


class GlossaryAddition(BaseModel):
    client: str | None  # None: the shared glossary every job gets
    line: str
    added: bool  # False when that exact line was already there


class RevisionConflict(Exception):
    """The record on disk has moved on since the browser last read it."""

    def __init__(self, current: Transcript) -> None:
        super().__init__(STALE_REVISION_DETAIL)
        self.current = current


def build_transcript_router(get_queue: Callable[[Request], JobQueue]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs/{job_id}/transcript", response_model=Transcript)
    async def get_transcript(job_id: str, queue: JobQueue = Depends(get_queue)) -> Transcript:
        job = job_or_404(queue, job_id)
        try:
            transcript = await run_in_threadpool(read_transcript, output_dir_of(job), _stem_of(job))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if transcript is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no saved transcript.")
        return transcript

    @router.patch("/api/jobs/{job_id}/transcript", response_model=None)
    async def patch_transcript(
        job_id: str, body: TranscriptPatch, queue: JobQueue = Depends(get_queue)
    ) -> JSONResponse:
        job = job_or_404(queue, job_id)
        try:
            updated = await run_in_threadpool(apply_ops, output_dir_of(job), job, body)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} has no saved transcript.")
        except RevisionConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"detail": STALE_REVISION_DETAIL, "transcript": exc.current.model_dump(mode="json")},
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return JSONResponse(status_code=200, content=updated.model_dump(mode="json"))

    @router.post("/api/jobs/{job_id}/glossary", response_model=GlossaryAddition, status_code=201)
    async def add_glossary_line(
        job_id: str,
        body: GlossaryFix,
        request: Request,
        queue: JobQueue = Depends(get_queue),
    ) -> GlossaryAddition:
        job = job_or_404(queue, job_id)
        glossaries = _glossary_provider(request)
        line = _glossary_line(body)
        client = validate_client_name(job.options.client) if job.options.client else None
        added = await run_in_threadpool(_add_or_400, glossaries, client, line)
        return GlossaryAddition(client=client, line=line, added=added)

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


def _add_or_400(glossaries: ClientGlossaryProvider, client: str | None, line: str) -> bool:
    """`add_glossary_line_to_file`, with its three failures turned into the
    same answers the clients page gives: a 400 naming the bad lines, a 501
    when this build's provider cannot reach the shared file, a 500 when the
    disk refused."""
    try:
        return add_glossary_line_to_file(glossaries, client, line)
    except GlossaryValidationFailedError as exc:
        raise HTTPException(status_code=400, detail=_problems_detail(exc.problems))
    except NotImplementedError:
        raise HTTPException(status_code=501, detail=NO_SHARED_GLOSSARY_DETAIL)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't write the glossary file: {exc.strerror or exc}.")


def _glossary_provider(request: Request) -> ClientGlossaryProvider:
    provider = getattr(request.app.state, "glossary_provider", None)
    if provider is None:
        raise HTTPException(status_code=501, detail=NO_SHARED_GLOSSARY_DETAIL)
    return provider


def _stem_of(job: Job) -> str | None:
    return Path(job.input_path).stem if job.input_path else None


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


def _load(path: Path):
    from ash_captions.app.transcript import TranscriptError, load_transcript

    try:
        return load_transcript(path)
    except TranscriptError as exc:
        raise ValueError(str(exc)) from exc


def _to_model(record: Any) -> Transcript:
    from ash_captions.app.transcript import word_style_to_dict

    meta = None
    if record.meta is not None:
        meta = [
            WordMetaOut(
                edited=m.edited,
                retimed=m.retimed,
                break_before=m.break_before,
                no_break_before=m.no_break_before,
                style=word_style_to_dict(m.style),
            )
            for m in record.meta
        ]
    return Transcript(
        language=record.language,
        words=[TranscriptWord(w=w.text, s=w.start, e=w.end, p=w.probability) for w in record.words],
        en_words=(
            [EnglishWord(w=w.text, s=w.start, e=w.end) for w in record.en_words]
            if record.en_words is not None
            else None
        ),
        revision=record.revision,
        meta=meta,
        en_stale=record.en_stale,
    )


def read_transcript(output_dir: Path, stem: str | None) -> Transcript | None:
    """None when there is no transcript file; ValueError (the loader's own
    message, naming the file) when there is one that cannot be read."""
    path = transcript_file(output_dir, stem)
    return None if path is None else _to_model(_load(path))


def _apply_one(record: Any, op: TranscriptOp) -> Any:
    from ash_captions.app import transcript as tx

    if op.op == "set_text":
        if op.text is None:
            raise ValueError("a set_text needs the new text")
        return tx.set_text(record, op.index, op.text, all_occurrences=op.all)
    if op.op == "retime":
        return tx.retime(record, op.index, start=op.start, end=op.end)
    if op.op == "split":
        return tx.split(record, op.index)
    if op.op == "merge":
        return tx.merge(record, op.index)
    return tx.set_style(record, op.index, op.style)


def apply_ops(output_dir: Path, job: Job, patch: TranscriptPatch) -> TranscriptUpdate:
    """Apply every op to one record, re-render the job's caption files from
    it, then save it.

    In that order deliberately: the record is the source of truth, so a
    crash between the two leaves stale outputs that the next successful
    edit rewrites, rather than a record nothing on disk agrees with.
    Raises `FileNotFoundError` (no transcript), `RevisionConflict` (another
    tab got there first) or `ValueError` (an unreadable file, a bad index
    or a bad value) -- and in every one of those cases nothing is written.
    """
    from ash_captions.app.runner_util import rewrite_outputs
    from ash_captions.app.transcript import save_transcript

    path = transcript_file(Path(output_dir), _stem_of(job))
    if path is None:
        raise FileNotFoundError(output_dir)
    record = _load(path)
    if record.revision != patch.revision:
        raise RevisionConflict(_to_model(record))
    for op in patch.ops:
        record = _apply_one(record, op)
    stem = path.name[: -len(TRANSCRIPT_SUFFIX)]
    position = _position_of(job)
    cards = rewrite_outputs(
        record, output_dir=Path(output_dir), stem=stem, preset=job.options.preset, position=position
    )
    save_transcript(path, record)
    return TranscriptUpdate(**_to_model(record).model_dump(), cards=cards)


def _position_of(job: Job) -> tuple[float, float] | None:
    x, y = job.options.caption_x, job.options.caption_y
    return (float(x), float(y)) if x is not None and y is not None else None


# --- the glossary, from the place the editor noticed the problem -----------


def _glossary_line(fix: GlossaryFix) -> str:
    """`wrong => right`, checked as a pair before it is a line: the
    per-line parser cannot tell a `=>` inside one side from the separator
    itself, so that is refused here."""
    left, right = fix.from_.strip(), fix.to.strip()
    for name, value in (("from", left), ("to", right)):
        if not value:
            raise HTTPException(status_code=400, detail=f"{name!r} cannot be empty.")
        if "=>" in value or "\n" in value or "\r" in value:
            raise HTTPException(status_code=400, detail=f"{name!r} cannot contain '=>' or a line break.")
        if value.startswith("#"):
            raise HTTPException(status_code=400, detail=f"{name!r} cannot start with '#' (that is a comment).")
    if left == right:
        raise HTTPException(status_code=400, detail="The two spellings are the same.")
    return f"{left} => {right}"


def _appended(text: str, line: str) -> str:
    body = text.replace("\r\n", "\n")
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{body}{line}\n"


def _already_there(text: str, line: str) -> bool:
    return any(existing.strip() == line for existing in text.replace("\r\n", "\n").split("\n"))


def add_glossary_line_to_file(glossaries: ClientGlossaryProvider, client: str | None, line: str) -> bool:
    """Append `line` to the client's glossary, or to the shared one when
    the job has no client. False when that exact line was already there.

    The client path is the provider call `PUT /api/clients/{c}/glossary`
    makes, so it validates identically. The shared file has no provider
    method (the protocol is client-keyed and the real implementation
    refuses to treat "glossary" as a client), so it is written here with
    the same validator and the same temp-and-replace.
    """
    if client:
        text = glossaries.read_glossary(client)
        if _already_there(text, line):
            return False
        glossaries.write_glossary(client, _appended(text, line))
        return True

    from ash_captions.languages import SHARED_GLOSSARY_FILENAME, validate_glossary_text

    directory = getattr(glossaries, "glossary_dir", None)
    if directory is None:
        raise NotImplementedError("this glossary provider cannot reach the shared file")
    path = Path(directory) / SHARED_GLOSSARY_FILENAME
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (FileNotFoundError, UnicodeDecodeError):
        text = ""
    if _already_there(text, line):
        return False
    new_text = _appended(text, line)
    problems = validate_glossary_text(new_text)
    if problems:
        raise GlossaryValidationFailedError(problems)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(new_text, encoding="utf-8")
    os.replace(partial, path)
    return True
