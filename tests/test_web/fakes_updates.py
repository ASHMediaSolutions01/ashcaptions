"""In-memory fakes for the in-app update flow (spec 11.4). Re-exported
from `fakes.py`, which is where tests import them from.

Real production checking is owned by `app/__main__.py`, which sets
`app.state.update_state` itself, after `create_app()` returns, to an
`ash_captions.app.updater.UpdateState` (see interfaces.py's module
docstring on this). Tests do the same thing: set `app.state.update_state`
to one of these fakes on the `app` fixture, mirroring production exactly
rather than routing it through `create_app()`'s constructor.
"""

from __future__ import annotations

import uuid
from typing import Any

from ash_captions.web.interfaces import UpdateApplyNotFoundError
from ash_captions.web.models import UpdateApplyJob, UpdateApplyStatus


class FakeUpdateInfo:
    """Stands in for `ash_captions.app.updater.UpdateInfo` structurally --
    only the attributes `app.py`/`update_adapter.py` actually read."""

    def __init__(
        self,
        *,
        version: str = "9.9.9",
        notes: str | None = "Bug fixes and performance improvements.",
        download_url: str = "https://example.invalid/update.zip",
        sha256: str = "deadbeef",
        size_bytes: int = 123_456_789,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        self.version = version
        self.notes = notes
        self.download_url = download_url
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.manifest = manifest or {}


class FakeUpdateState:
    """Stands in for `ash_captions.app.updater.UpdateState`."""

    def __init__(self, info: FakeUpdateInfo | None = None) -> None:
        self._info = info

    def get(self) -> FakeUpdateInfo | None:
        return self._info

    def set(self, info: FakeUpdateInfo | None) -> None:
        self._info = info


class FakeUpdateApplier:
    """Implements the `UpdateApplier` protocol in memory -- no network
    download, no zip extraction, no detached restart helper. Jobs stay
    `pending` until a test calls `force_status()`, same pattern as
    `FakeJobQueue`/`FakePreviewRenderer`."""

    def __init__(self) -> None:
        self._jobs: dict[str, UpdateApplyJob] = {}
        self.submitted: list[Any] = []  # the `update` object each submit_apply() call received
        self.has_running_job_callbacks: list[Any] = []  # the has_running_job each call received

    def submit_apply(self, update: Any, *, has_running_job: Any = None) -> UpdateApplyJob:
        self.submitted.append(update)
        self.has_running_job_callbacks.append(has_running_job)
        job = UpdateApplyJob(id=uuid.uuid4().hex, status=UpdateApplyStatus.PENDING)
        self._jobs[job.id] = job
        return job

    def get_apply_status(self, job_id: str) -> UpdateApplyJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise UpdateApplyNotFoundError(job_id)
        return job

    # Test helper only -- not part of the UpdateApplier protocol.
    def force_status(self, job_id: str, status: UpdateApplyStatus, **fields: Any) -> UpdateApplyJob:
        job = self._jobs[job_id]
        updated = job.model_copy(update={"status": status, **fields})
        self._jobs[job_id] = updated
        return updated
