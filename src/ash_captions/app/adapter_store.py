"""``NotifyingStore``: the ``pipeline.JobStore`` wrapper ``JobWorker`` is
handed so every state change also wakes the control page's SSE
subscribers (see ``adapter.QueueAdapter`` and its module docstring on the
push-driven contract). Split out of ``adapter.py`` for size."""

from __future__ import annotations

from typing import Callable

from ash_captions.pipeline.db import JobStore


class NotifyingStore:
    """Wraps a ``pipeline.JobStore`` so ``JobWorker``'s state-changing calls
    also publish to SSE subscribers. Everything else is forwarded
    unchanged -- ``JobWorker`` only calls the methods overridden below
    plus ``fetch_oldest_pending``/``reset_stale_running`` (passed through
    via ``__getattr__``), never anything requiring real ``JobStore``
    typing.
    """

    def __init__(
        self,
        store: JobStore,
        notify: Callable[[], None],
        on_finished: Callable[[int], None] | None = None,
    ) -> None:
        self._store = store
        self._notify = notify
        self._on_finished = on_finished or (lambda job_id: None)
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
        self._on_finished(job_id)

    def mark_failed(self, job_id: int, error: str) -> None:
        self._last_progress.pop(job_id, None)
        self._store.mark_failed(job_id, error)
        self._notify()
        self._on_finished(job_id)

    def requeue(self, job_id: int) -> None:
        self._last_progress.pop(job_id, None)
        self._store.requeue(job_id)
        self._notify()
