"""In-memory fakes for the queue and language catalogue protocols the web
layer depends on (see `ash_captions.web.interfaces`). No SQLite, no ffmpeg,
no model -- just enough behaviour to exercise the API."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from ash_captions.web.interfaces import JobNotFoundError, JobNotRetryableError
from ash_captions.web.models import Dialect, Job, JobOptions, JobStatus, Language


class FakeJobQueue:
    """Implements the `JobQueue` protocol in memory."""

    def __init__(self, jobs: list[Job] | None = None) -> None:
        self._jobs: dict[str, Job] = {j.id: j for j in (jobs or [])}
        self._subscribers: list[asyncio.Queue] = []

    def list_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def submit(self, file_path: Path, options: JobOptions) -> Job:
        now = datetime.now(timezone.utc)
        job = Job(
            id=uuid.uuid4().hex,
            filename=file_path.name,
            status=JobStatus.PENDING,
            progress=0.0,
            options=options,
            error=None,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.id] = job
        self._notify()
        return job

    def retry(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.FAILED:
            raise JobNotRetryableError(job_id)
        updated = job.model_copy(
            update={
                "status": JobStatus.PENDING,
                "error": None,
                "progress": 0.0,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._jobs[job_id] = updated
        self._notify()
        return updated

    async def subscribe(self) -> AsyncIterator[list[Job]]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            yield self.list_jobs()
            while True:
                snapshot = await queue.get()
                yield snapshot
        finally:
            self._subscribers.remove(queue)

    def _notify(self) -> None:
        snapshot = self.list_jobs()
        for subscriber in self._subscribers:
            subscriber.put_nowait(snapshot)

    # Test helper only -- not part of the JobQueue protocol.
    def force_status(self, job_id: str, status: JobStatus, **fields: object) -> Job:
        job = self._jobs[job_id]
        updated = job.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc), **fields}
        )
        self._jobs[job_id] = updated
        self._notify()
        return updated


def default_languages() -> list[Language]:
    return [
        Language(
            code="en",
            label="English",
            band="flagship",
            dialects=[
                Dialect(code="en-US", label="US"),
                Dialect(code="en-UK", label="UK"),
            ],
        ),
        Language(
            code="es",
            label="Spanish",
            band="flagship",
            dialects=[
                Dialect(code="es-MX", label="Mexico"),
                Dialect(code="es-ES", label="Spain"),
            ],
        ),
        Language(
            code="pt",
            label="Portuguese",
            band="flagship",
            dialects=[
                Dialect(code="pt-BR", label="Brazil"),
                Dialect(code="pt-PT", label="Portugal"),
            ],
        ),
        Language(code="fr", label="French", band="flagship", dialects=[]),
    ]


class FakeLanguageCatalogue:
    """Implements the `LanguageCatalogueProvider` protocol in memory."""

    def __init__(self, languages: list[Language] | None = None) -> None:
        self._languages = languages if languages is not None else default_languages()

    def list_languages(self) -> list[Language]:
        return self._languages
