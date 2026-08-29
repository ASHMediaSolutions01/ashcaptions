"""Single-worker background queue that drains pending jobs from the DB.

This module never imports the transcription engine. The caller hands in a
plain callable (``run_job``) that does the actual work; ``JobWorker`` only
owns job lifecycle: pulling the oldest pending job, marking it running,
invoking the callable, and recording done/failed. Video work is serialised
by design — one worker thread, one job at a time (spec section 8.1: "Queue
... Must survive close-and-reopen").
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .db import Job, JobStore

# Reports 0-100 progress for the currently running job.
ProgressCallback = Callable[[int], None]

# Executes one job to completion. Must raise on failure (the exception's
# str() becomes the job's stored error) and return normally on success.
# Must not be called concurrently with itself — the worker guarantees that.
RunJob = Callable[[Job, ProgressCallback], None]


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
        self._thread: threading.Thread | None = None

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

        def report_progress(percent: int) -> None:
            self._store.mark_progress(job.id, percent)

        try:
            self._run_job(job, report_progress)
        except Exception as exc:  # noqa: BLE001 - any engine failure must be captured, not crash the worker
            # A failed job stays visible with its error; it is never
            # silently dropped (spec section 12).
            self._store.mark_failed(job.id, str(exc))
            return True

        self._store.mark_done(job.id)
        return True

    def start(self) -> None:
        """Run crash recovery, then start the background worker thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("JobWorker is already running")
        self.recover()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ash-captions-queue-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Signal the worker loop to stop and wait for the current job to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            processed = self.process_next()
            if not processed:
                self._sleep_fn(self._poll_interval)
