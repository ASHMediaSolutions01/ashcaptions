"""Dependency contracts the web layer relies on.

The web layer never imports the queue or the language catalogue directly --
those belong to other modules (`engine`, and whatever owns the SQLite-backed
queue). Instead it depends on these protocols, and a concrete implementation
is injected into `create_app()`. Tests inject fakes; production wiring
(outside this package) injects the real queue and catalogue.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Protocol, runtime_checkable

from .models import Job, JobOptions, Language


class JobNotFoundError(Exception):
    """Raised by a JobQueue implementation when a job id does not exist."""


class JobNotRetryableError(Exception):
    """Raised by a JobQueue implementation when retry() is called on a job
    that is not currently in the `failed` state."""


@runtime_checkable
class JobQueue(Protocol):
    """What the web layer needs from the queue.

    Implemented elsewhere (SQLite table + worker thread per spec §8.1). The
    web layer only ever reads state and enqueues/retries -- it never runs a
    job itself.
    """

    def list_jobs(self) -> list[Job]:
        """Return the current queue snapshot, newest first."""
        ...

    def get_job(self, job_id: str) -> Job | None:
        """Return a single job, or None if it does not exist."""
        ...

    def submit(self, file_path: Path, options: JobOptions) -> Job:
        """Enqueue a new job for `file_path` and return it as `pending`.

        Implementations own validating that `file_path` exists and is
        readable; the web layer already checked existence and extension
        before calling this, but the queue is the source of truth.
        """
        ...

    def retry(self, job_id: str) -> Job:
        """Requeue a failed job. Raises JobNotFoundError or
        JobNotRetryableError as appropriate; the web layer maps both to HTTP
        responses."""
        ...

    def subscribe(self) -> AsyncIterator[list[Job]]:
        """Yield a queue snapshot each time job state changes.

        Must be push-driven (e.g. backed by an asyncio.Condition/Queue), not
        a poll loop -- the SSE endpoint awaits this directly and must not
        busy-loop (spec §8.3).
        """
        ...


@runtime_checkable
class LanguageCatalogueProvider(Protocol):
    """What the web layer needs from the language + dialect catalogue
    (spec §7). Implemented in `ash_captions.languages`.
    """

    def list_languages(self) -> list[Language]:
        """Return every supported language with its dialect presets."""
        ...
