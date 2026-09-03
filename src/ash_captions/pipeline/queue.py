"""Single-worker background queue that drains pending jobs from the DB.

This module never imports the transcription engine. The caller hands in a
plain callable (``run_job``) that does the actual work; ``JobWorker`` only
owns job lifecycle: pulling the oldest pending job, marking it running,
invoking the callable, and recording done/failed. Video work is serialised
by design — one worker thread, one job at a time (spec section 8.1: "Queue
... Must survive close-and-reopen").

Two guarantees that only matter on the day something goes wrong:

* The loop never dies. A raising store call (a locked database, a full
  disk) is logged and retried with backoff -- an hour-long job must never
  sit ``pending`` forever behind a dead thread with nothing in the log.
* ``stop()`` cancels, it does not abandon. The running job's callable is
  handed a ``should_stop`` it polls; when it gives up it raises
  ``JobCancelled`` and the job goes back to ``pending`` (not ``failed``)
  so it re-runs on the next launch.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .db import Job, JobStore

log = logging.getLogger("ash_captions.pipeline.queue")

# Reports 0-100 progress for the currently running job. The object the
# worker actually passes is a ``ProgressReporter`` -- still callable with
# an int, but also carrying ``stage()`` and ``should_stop()``.
ProgressCallback = Callable[[int], None]

# Runs once the job has been marked done -- the place to delete a consumed
# input, which must never happen before the row says ``done``.
AfterDone = Callable[[], None]

# Executes one job to completion. Must raise on failure (the exception's
# str() becomes the job's stored error) and return normally on success,
# optionally returning an ``AfterDone`` callable. Must not be called
# concurrently with itself — the worker guarantees that.
RunJob = Callable[[Job, ProgressCallback], "AfterDone | None"]

# Backoff when the loop body itself raises (outside run_job).
_BACKOFF_INITIAL_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0
# Room for the engine to notice should_stop() (it polls per segment) and
# kill its ffmpeg child before we give up waiting.
DEFAULT_STOP_TIMEOUT_SECONDS = 30.0

STDERR_TAIL_LINES = 20
STDERR_TAIL_MAX_BYTES = 4096


class JobCancelled(Exception):
    """Raised (or translated to) by ``run_job`` when ``should_stop()`` was
    honoured. The worker puts the job back to ``pending``."""


class ProgressReporter:
    """What ``run_job`` receives as its second argument.

    Callable with a 0-100 int (the original contract, so any plain
    ``report(pct)`` caller keeps working) and additionally exposes the
    current pipeline stage and the cancellation flag.
    """

    def __init__(
        self,
        on_progress: Callable[[int], None],
        on_stage: Callable[[str], None],
        should_stop: Callable[[], bool],
    ) -> None:
        self._on_progress = on_progress
        self._on_stage = on_stage
        self._should_stop = should_stop

    def __call__(self, percent: int) -> None:
        self._on_progress(percent)

    def stage(self, name: str) -> None:
        self._on_stage(name)

    def should_stop(self) -> bool:
        return self._should_stop()


def format_job_error(exc: BaseException) -> str:
    """``str(exc)`` plus, when the exception carries a ``stderr`` attribute
    (engine's ffmpeg errors do), its last lines -- the part that says
    *why* ffmpeg failed, which ``str()`` omits. Capped so a runaway log
    can't bloat the jobs table."""
    message = str(exc).strip() or exc.__class__.__name__
    stderr = getattr(exc, "stderr", None)
    if not isinstance(stderr, str) or not stderr.strip():
        return message
    tail = "\n".join(stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
    combined = f"{message}\n--- ffmpeg stderr (tail) ---\n{tail}"
    if len(combined.encode("utf-8")) > STDERR_TAIL_MAX_BYTES:
        combined = combined.encode("utf-8")[-STDERR_TAIL_MAX_BYTES:].decode("utf-8", "ignore")
    return combined


class JobWorker:
    """Pulls and executes jobs one at a time on a single background thread."""

    def __init__(
        self,
        store: JobStore,
        run_job: RunJob,
        *,
        poll_interval: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._store = store
        self._run_job = run_job
        self._poll_interval = poll_interval
        self._sleep_fn = sleep_fn
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_job_id: int | None = None
        self.last_error: str | None = None
        self.last_error_at: float | None = None

    # -- health ------------------------------------------------------------

    def is_alive(self) -> bool:
        """True while the background thread is running."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def current_job_id(self) -> int | None:
        return self._current_job_id

    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    # -- lifecycle ---------------------------------------------------------

    def recover(self) -> list[int]:
        """Crash-recovery pass (spec section 12).

        Resets any job stuck in ``running`` back to ``pending`` since it was
        interrupted and its partial ffmpeg output is not trusted. Safe to
        call multiple times. ``start()`` calls this automatically before
        beginning the worker loop; tests can call it directly.
        """
        return self._store.reset_stale_running()

    def process_next(self) -> bool:
        """Run a single oldest-pending job to completion, synchronously.

        Returns True if a job was found and processed (regardless of
        success/failure), False if the queue was empty. Exposed separately
        from the background loop so tests can drive the worker one job at a
        time without threads or real sleeps.
        """
        job = self._store.fetch_oldest_pending()
        if job is None:
            return False

        self._store.mark_running(job.id)
        self._current_job_id = job.id
        reporter = ProgressReporter(
            on_progress=lambda pct: self._store.mark_progress(job.id, pct),
            on_stage=lambda name: self._store.mark_stage(job.id, name),
            should_stop=self._cancel_event.is_set,
        )

        try:
            after_done = self._run_job(job, reporter)
        except JobCancelled:
            self._requeue_cancelled(job)
            return True
        except Exception as exc:  # noqa: BLE001 - any engine failure must be captured, not crash the worker
            if self._cancel_event.is_set():
                # The engine in use may not raise JobCancelled itself; a
                # failure while we were asking it to stop is treated as
                # the cancellation it almost certainly is.
                log.info("job %d interrupted by shutdown (%s); requeued", job.id, exc)
                self._requeue_cancelled(job)
                return True
            # A failed job stays visible with its error; it is never
            # silently dropped (spec section 12).
            log.exception("job %d failed (%s)", job.id, job.input_path)
            self._store.mark_failed(job.id, format_job_error(exc))
            return True
        finally:
            self._current_job_id = None

        self._store.mark_done(job.id)
        if after_done is not None:
            try:
                after_done()
            except Exception:  # noqa: BLE001 - the job is done; cleanup failing is a log line, not a failure
                log.exception("post-completion cleanup for job %d failed", job.id)
        return True

    def _requeue_cancelled(self, job: Job) -> None:
        log.info("job %d cancelled by shutdown; back to pending for the next launch", job.id)
        self._store.requeue(job.id)

    def start(self) -> None:
        """Run crash recovery, then start the background worker thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("JobWorker is already running")
        self.recover()
        self._stop_event.clear()
        self._cancel_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ash-captions-queue-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float | None = DEFAULT_STOP_TIMEOUT_SECONDS) -> None:
        """Ask the running job to stop, then wait for the loop to exit.

        The cancel flag is what ``should_stop()`` reads inside the engine;
        the job is requeued as ``pending`` once it gives up. ``timeout=None``
        waits indefinitely.
        """
        self._stop_event.set()
        self._cancel_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("queue worker did not stop within %s s", timeout)
            self._thread = None

    def _loop(self) -> None:
        backoff = _BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            try:
                processed = self.process_next()
            except Exception as exc:  # noqa: BLE001 - the loop must outlive any single failure
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                self.last_error_at = time.time()
                log.exception("queue worker loop error; retrying in %.0f s", backoff)
                self._sleep_fn(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_SECONDS)
                continue
            backoff = _BACKOFF_INITIAL_SECONDS
            if not processed:
                self._sleep_fn(self._poll_interval)
