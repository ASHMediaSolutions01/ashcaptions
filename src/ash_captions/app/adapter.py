"""Bridges web's ``JobQueue`` protocol onto ``pipeline.JobStore`` (spec
section 8).

Four real impedance mismatches sit between the two layers:

1. **Id type** -- web's ``Job.id`` is ``str``; pipeline's is a SQLite
   ``int``. Converted both ways; a non-numeric id is treated as "not
   found" rather than letting ``ValueError`` escape.
2. **Progress scale** -- web's ``Job.progress`` is ``0.0-1.0``; pipeline's
   ``mark_progress`` takes ``0-100``. Converted and clamped so a rounding
   quirk can never produce a value Pydantic rejects.
3. **Options shape** -- web's ``JobOptions`` is a Pydantic model;
   pipeline's is a frozen dataclass. Mapped field by field.
4. **Filename** -- pipeline's ``Job`` has no ``filename``; it is derived
   from the input path's basename.

Push-driven SSE
----------------
``subscribe()`` must never poll (spec section 8.3). Each subscriber gets
its own ``asyncio.Queue``; ``notify()`` (called on every state change --
see ``_NotifyingStore`` below) pushes a fresh snapshot into every
subscriber's queue. The queue worker thread that actually processes jobs
runs *outside* the event loop, so publishing from it must be marshalled
back with ``loop.call_soon_threadsafe`` -- calling ``asyncio.Queue.put``
directly from a foreign thread is not safe and either deadlocks or
silently corrupts the queue's internal state.

``pipeline.queue.JobWorker`` only ever calls a handful of methods on the
store object it is given (``fetch_oldest_pending``, ``mark_running``,
``mark_progress``, ``mark_done``, ``mark_failed``, ``reset_stale_running``)
and never checks its type, so ``notifying_store`` -- a thin wrapper that
forwards to the real ``JobStore`` and then calls ``notify()`` -- can stand
in for it without any change to ``pipeline.queue`` or ``pipeline.db``.
Hand ``notifying_store`` to ``JobWorker`` in ``__main__.py``; keep using
this adapter itself (backed by the real, unwrapped store) for the web
layer.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from ash_captions.pipeline.db import Job as PipelineJob
from ash_captions.pipeline.db import JobOptions as PipelineJobOptions
from ash_captions.pipeline.db import JobStatus as PipelineJobStatus
from ash_captions.pipeline.db import JobStore
from ash_captions.web.interfaces import JobNotFoundError, JobNotRetryableError
from ash_captions.web.models import Job as WebJob
from ash_captions.web.models import JobOptions as WebJobOptions
from ash_captions.web.models import JobStatus as WebJobStatus


class QueueAdapter:
    """Implements web's ``JobQueue`` protocol over ``pipeline.JobStore``.

    ``out_dir`` is the retail output root (``settings.out_dir``); each
    submitted job gets its own ``out_dir/<video stem>`` subfolder (spec
    section 10), computed here so callers (the web routes, the watch-folder
    callback) never have to know that convention.
    """

    def __init__(self, store: JobStore, *, out_dir: Path) -> None:
        self._store = store
        self._out_dir = Path(out_dir)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        # Hand this to JobWorker in place of the raw store -- see module
        # docstring. Kept as a public attribute (not a method) since it is
        # a long-lived object identity JobWorker holds onto, not a call.
        self.notifying_store = _NotifyingStore(store, self._notify)

    # -- JobQueue protocol -----------------------------------------------

    def list_jobs(self) -> list[WebJob]:
        # pipeline.JobStore.list_jobs() already orders newest-first.
        return [_to_web_job(job) for job in self._store.list_jobs()]

    def get_job(self, job_id: str) -> WebJob | None:
        numeric_id = _parse_job_id(job_id)
        if numeric_id is None:
            return None
        job = self._store.get_job(numeric_id)
        return _to_web_job(job) if job is not None else None

    def submit(self, file_path: Path, options: WebJobOptions) -> WebJob:
        self._capture_loop()
        file_path = Path(file_path)
        output_dir = self._out_dir / file_path.stem
        job_id = self._store.insert_job(file_path, output_dir, _to_pipeline_options(options))
        job = self._store.get_job(job_id)
        assert job is not None, "insert_job returned an id that get_job can't find"
        self._notify()
        return _to_web_job(job)

    def retry(self, job_id: str) -> WebJob:
        self._capture_loop()
        numeric_id = _parse_job_id(job_id)
        if numeric_id is None:
            raise JobNotFoundError(job_id)
        job = self._store.get_job(numeric_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != PipelineJobStatus.FAILED:
            raise JobNotRetryableError(job_id)

        if hasattr(self._store, "requeue"):
            # Preferred path once pipeline.JobStore grows a single-job
            # requeue primitive -- see _requeue_job_row's docstring.
            self._store.requeue(numeric_id)
        else:
            _requeue_job_row(self._store.db_path, numeric_id)

        updated = self._store.get_job(numeric_id)
        assert updated is not None, "job disappeared between requeue and re-fetch"
        self._notify()
        return _to_web_job(updated)

    async def subscribe(self) -> AsyncIterator[list[WebJob]]:
        self._capture_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield self.list_jobs()
            while True:
                snapshot = await queue.get()
                yield snapshot
        finally:
            self._subscribers.discard(queue)

    # -- push plumbing -----------------------------------------------------

    def _capture_loop(self) -> None:
        """Remember the running event loop the first time we're called from
        one. ``submit``/``retry`` run synchronously inside an ``async def``
        FastAPI route handler (never thread-pooled -- see web/app.py), and
        ``subscribe`` is itself a coroutine, so all three genuinely run on
        the loop thread the first time they're invoked.
        """
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def _notify(self) -> None:
        """Wake every subscriber with a fresh snapshot.

        Safe to call from any thread: with no loop captured yet (nobody has
        subscribed, or a request hasn't happened yet) this is a no-op --
        there is nothing to wake, and the next subscriber's initial
        snapshot will already reflect current state. Once a loop is known,
        publishing always goes through ``call_soon_threadsafe``, which is
        the only safe way to touch an ``asyncio.Queue`` from the queue
        worker's background thread (spec section 8.3's push requirement).
        """
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._publish)

    def _publish(self) -> None:
        snapshot = self.list_jobs()
        for subscriber in list(self._subscribers):
            subscriber.put_nowait(snapshot)


class _NotifyingStore:
    """Wraps a ``pipeline.JobStore`` so ``JobWorker``'s state-changing calls
    also publish to SSE subscribers. Everything else is forwarded
    unchanged -- ``JobWorker`` only calls the five methods overridden
    below plus ``fetch_oldest_pending``/``reset_stale_running`` (passed
    through via ``__getattr__``), never anything requiring real
    ``JobStore`` typing.
    """

    def __init__(self, store: JobStore, notify) -> None:
        self._store = store
        self._notify = notify

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def mark_running(self, job_id: int) -> None:
        self._store.mark_running(job_id)
        self._notify()

    def mark_progress(self, job_id: int, progress: int) -> None:
        self._store.mark_progress(job_id, progress)
        self._notify()

    def mark_done(self, job_id: int) -> None:
        self._store.mark_done(job_id)
        self._notify()

    def mark_failed(self, job_id: int, error: str) -> None:
        self._store.mark_failed(job_id, error)
        self._notify()


# -- conversions -------------------------------------------------------------


def _parse_job_id(job_id: str) -> int | None:
    try:
        return int(job_id)
    except (TypeError, ValueError):
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _to_web_options(options: PipelineJobOptions) -> WebJobOptions:
    return WebJobOptions(
        language=options.language,
        dialect=options.dialect,
        preset=options.preset,
        burn_in=options.burn,
        translate_to_english=options.translate,
    )


def _to_pipeline_options(options: WebJobOptions) -> PipelineJobOptions:
    return PipelineJobOptions(
        language=options.language,
        dialect=options.dialect,
        preset=options.preset,
        burn=options.burn_in,
        translate=options.translate_to_english,
    )


def _to_web_job(job: PipelineJob) -> WebJob:
    # pipeline.Job has no single "last touched" timestamp; the most recent
    # of finished/started/created is the closest honest equivalent.
    updated_raw = job.finished_at or job.started_at or job.created_at
    return WebJob(
        id=str(job.id),
        filename=Path(job.input_path).name,
        status=WebJobStatus(job.status.value),
        progress=_clamp01(job.progress / 100.0),
        options=_to_web_options(job.options),
        error=job.error,
        created_at=datetime.fromisoformat(job.created_at),
        updated_at=datetime.fromisoformat(updated_raw),
    )


def _requeue_job_row(db_path: Path, job_id: int) -> None:
    """Reset one job back to ``pending``, clearing progress/error/timestamps
    -- the single-job equivalent of ``JobStore.reset_stale_running()``,
    which only resets every ``running`` job at once.

    ``pipeline.JobStore`` doesn't expose this as a public method today.
    This is a narrow, self-contained bridge (same connection settings as
    ``JobStore._connect()``) rather than reaching into that class's
    private internals; it goes away in favour of a real
    ``store.requeue(job_id)`` call (see ``QueueAdapter.retry``'s
    ``hasattr`` check above) as soon as pipeline grows one -- flagged to
    the pipeline owner.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            "UPDATE jobs SET status = ?, progress = 0, error = NULL, "
            "started_at = NULL, finished_at = NULL WHERE id = ?",
            (PipelineJobStatus.PENDING.value, job_id),
        )
        conn.commit()
    finally:
        conn.close()
