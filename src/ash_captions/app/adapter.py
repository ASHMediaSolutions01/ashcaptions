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

Publishing is throttled to one snapshot per ``notify_interval`` (trailing
edge, so the last value always lands): ffmpeg reports progress several
times a second, and each publish is a full ``SELECT`` plus a JSON encode
per open tab, on the event loop -- for an hour-long burn that adds up.
``_NotifyingStore.mark_progress`` additionally skips the write itself
when the integer percentage hasn't changed.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ash_captions.pipeline.db import DuplicateJobError
from ash_captions.pipeline.db import Job as PipelineJob
from ash_captions.pipeline.db import JobOptions as PipelineJobOptions
from ash_captions.pipeline.db import JobStatus as PipelineJobStatus
from ash_captions.pipeline.db import JobStore
from ash_captions.web.interfaces import JobNotFoundError, JobNotRetryableError
from ash_captions.web.models import Job as WebJob
from ash_captions.web.models import JobOptions as WebJobOptions
from ash_captions.web.models import JobStatus as WebJobStatus

# The control page never needs the whole history; the newest rows are
# what an editor is looking at.
DEFAULT_LIST_LIMIT = 200
DEFAULT_NOTIFY_INTERVAL_SECONDS = 1.0

# Extra fields the web Job model may grow; set only when it declares them
# (a plain pydantic model silently drops unknown kwargs, which would hide
# a wiring gap rather than surface it).
_OPTIONAL_WEB_FIELDS = ("stage", "stage_started_at", "started_at", "input_path", "output_dir")

_SUFFIX_RE = re.compile(r"^(.*) \((\d+)\)$")


class QueueAdapter:
    """Implements web's ``JobQueue`` protocol over ``pipeline.JobStore``.

    ``out_dir`` is the retail output root (``settings.out_dir``); each
    submitted job gets its own ``out_dir/<video stem>`` subfolder (spec
    section 10) -- made unique (``<stem> (2)``, ``<stem> (3)``...) against
    both the disk and every job row, so two videos sharing a stem never
    overwrite each other's outputs.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        out_dir: Path,
        notify_interval: float = DEFAULT_NOTIFY_INTERVAL_SECONDS,
        list_limit: int = DEFAULT_LIST_LIMIT,
    ) -> None:
        self._store = store
        self._out_dir = Path(out_dir)
        self._notify_interval = notify_interval
        self._list_limit = list_limit
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._last_publish: float = 0.0
        self._trailing_handle: asyncio.TimerHandle | None = None
        self._health_sources: dict[str, Any] = {}
        # Hand this to JobWorker in place of the raw store -- see module
        # docstring. Kept as a public attribute (not a method) since it is
        # a long-lived object identity JobWorker holds onto, not a call.
        self.notifying_store = _NotifyingStore(store, self._notify)

    # -- JobQueue protocol -----------------------------------------------

    def list_jobs(self) -> list[WebJob]:
        # pipeline.JobStore.list_jobs() already orders newest-first.
        return [_to_web_job(job) for job in self._store.list_jobs(limit=self._list_limit)]

    def get_job(self, job_id: str) -> WebJob | None:
        numeric_id = _parse_job_id(job_id)
        if numeric_id is None:
            return None
        job = self._store.get_job(numeric_id)
        return _to_web_job(job) if job is not None else None

    def submit(self, file_path: Path, options: WebJobOptions) -> WebJob:
        """Enqueue ``file_path``. A file that already has a ``pending`` or
        ``running`` job is not queued again; its existing job is returned."""
        self._capture_loop()
        file_path = Path(file_path)
        existing = self._store.find_live_job(file_path)
        if existing is not None:
            return _to_web_job(existing)
        output_dir = self.unique_output_dir(file_path.stem)
        job_id = self._store.insert_job(file_path, output_dir, _to_pipeline_options(options))
        job = self._store.get_job(job_id)
        assert job is not None, "insert_job returned an id that get_job can't find"
        self._notify()
        return _to_web_job(job)

    def unique_output_dir(self, stem: str) -> Path:
        """``out_dir/<stem>``, or the first ``<stem> (n)`` not yet used on
        disk or by any job row."""
        candidate = self._out_dir / stem
        n = 1
        while candidate.exists() or self._store.output_dir_in_use(candidate):
            n += 1
            candidate = self._out_dir / f"{stem} ({n})"
        return candidate

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

        try:
            self._store.requeue(numeric_id)
        except DuplicateJobError as exc:
            raise JobNotRetryableError(str(exc)) from exc

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

    # -- health (read by the control page, outside the JobQueue protocol) --

    def attach_health(self, *, worker: Any = None, watcher: Any = None) -> None:
        """Give ``health()`` the worker/watcher to report on."""
        self._health_sources = {"worker": worker, "watcher": watcher}

    def health(self) -> dict[str, Any]:
        """Plain dict for a status line: ``worker_alive``,
        ``worker_last_error``, ``last_watcher_poll`` (ISO-8601 or None)."""
        worker = self._health_sources.get("worker")
        watcher = self._health_sources.get("watcher")
        last_poll = getattr(watcher, "last_poll_at", None) if watcher is not None else None
        return {
            "worker_alive": bool(worker.is_alive()) if worker is not None else False,
            "worker_last_error": getattr(worker, "last_error", None) if worker is not None else None,
            "current_job_id": getattr(worker, "current_job_id", None) if worker is not None else None,
            "watcher_alive": bool(watcher.is_alive()) if watcher is not None else False,
            "last_watcher_poll": _iso_or_none(last_poll),
        }

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
        """Wake every subscriber with a fresh snapshot (rate-limited).

        Safe to call from any thread: with no loop captured yet (nobody has
        subscribed, or a request hasn't happened yet) this is a no-op --
        there is nothing to wake, and the next subscriber's initial
        snapshot will already reflect current state. Once a loop is known,
        publishing always goes through ``call_soon_threadsafe``, which is
        the only safe way to touch an ``asyncio.Queue`` from the queue
        worker's background thread (spec section 8.3's push requirement).
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._schedule_publish)
        except RuntimeError:
            # Loop shut down between the check and the call (app exiting).
            return

    def _schedule_publish(self) -> None:
        """On the loop thread: publish now if the interval has elapsed,
        otherwise arm one trailing publish for when it does."""
        if self._trailing_handle is not None:
            return  # a trailing publish is already armed; it will carry this change
        elapsed = time.monotonic() - self._last_publish
        if elapsed >= self._notify_interval:
            self._publish()
            return
        loop = self._loop
        assert loop is not None
        self._trailing_handle = loop.call_later(self._notify_interval - elapsed, self._publish_trailing)

    def _publish_trailing(self) -> None:
        self._trailing_handle = None
        self._publish()

    def _publish(self) -> None:
        self._last_publish = time.monotonic()
        if not self._subscribers:
            return
        snapshot = self.list_jobs()
        for subscriber in list(self._subscribers):
            subscriber.put_nowait(snapshot)


class _NotifyingStore:
    """Wraps a ``pipeline.JobStore`` so ``JobWorker``'s state-changing calls
    also publish to SSE subscribers. Everything else is forwarded
    unchanged -- ``JobWorker`` only calls the methods overridden below
    plus ``fetch_oldest_pending``/``reset_stale_running`` (passed through
    via ``__getattr__``), never anything requiring real ``JobStore``
    typing.
    """

    def __init__(self, store: JobStore, notify: Callable[[], None]) -> None:
        self._store = store
        self._notify = notify
        self._last_progress: dict[int, int] = {}

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def mark_running(self, job_id: int) -> None:
        self._last_progress.pop(job_id, None)
        self._store.mark_running(job_id)
        self._notify()

    def mark_progress(self, job_id: int, progress: int) -> None:
        if self._last_progress.get(job_id) == progress:
            return  # ffmpeg reports several times a second; same integer, no write
        self._last_progress[job_id] = progress
        self._store.mark_progress(job_id, progress)
        self._notify()

    def mark_stage(self, job_id: int, stage: str) -> None:
        self._store.mark_stage(job_id, stage)
        self._notify()

    def mark_done(self, job_id: int) -> None:
        self._last_progress.pop(job_id, None)
        self._store.mark_done(job_id)
        self._notify()

    def mark_failed(self, job_id: int, error: str) -> None:
        self._last_progress.pop(job_id, None)
        self._store.mark_failed(job_id, error)
        self._notify()

    def requeue(self, job_id: int) -> None:
        self._last_progress.pop(job_id, None)
        self._store.requeue(job_id)
        self._notify()


# -- conversions -------------------------------------------------------------


def _parse_job_id(job_id: str) -> int | None:
    try:
        return int(job_id)
    except (TypeError, ValueError):
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _iso_or_none(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


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
    # of finished/stage-started/started/created is the closest honest equivalent.
    updated_raw = job.finished_at or job.stage_started_at or job.started_at or job.created_at
    extras = {
        "stage": job.stage,
        "stage_started_at": job.stage_started_at,
        "started_at": job.started_at,
        "input_path": job.input_path,
        "output_dir": job.output_dir,
    }
    declared = getattr(WebJob, "model_fields", {})
    optional = {name: extras[name] for name in _OPTIONAL_WEB_FIELDS if name in declared}
    return WebJob(
        id=str(job.id),
        filename=Path(job.input_path).name,
        status=WebJobStatus(job.status.value),
        progress=_clamp01(job.progress / 100.0),
        options=_to_web_options(job.options),
        error=job.error,
        created_at=datetime.fromisoformat(job.created_at),
        updated_at=datetime.fromisoformat(updated_raw),
        **optional,
    )
