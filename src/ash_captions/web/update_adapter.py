"""Production implementation of web's `UpdateApplier` protocol (spec 11.4):
downloads, verifies, and applies an in-app update after an editor's click.

Consumes `ash_captions.app.updater`'s public download/verify/apply functions
directly -- the one place under `web/` allowed to, mirroring
`styles_adapter.py`/`preview_adapter.py`'s relationship to their packages.
`app.py` itself only ever touches `request.app.state.update_state`
structurally and this protocol, never `ash_captions.app.updater` types, so
it stays decoupled the same way it is from `ash_captions.styles`/
`ash_captions.engine` (see `interfaces.py`'s module docstring on this).

Checking for an update -- the background thread, `UpdateState`, and
publishing the result onto `app.state.update_state` -- is `app/__main__.py`'s
job, already built and wired before this module ever runs (see that
module's own comment: "so a future control-page route can read the last
check's result"). This module only covers what happens *after* an editor
clicks "Update now": download, verify, apply, restart.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ash_captions.app.updater import UpdateApplyError, apply_update, download_and_verify_update
from ash_captions.config import data_root

from .interfaces import UpdateApplyBusyError, UpdateApplyNotFoundError
from .models import UpdateApplyJob, UpdateApplyStatus

logger = logging.getLogger(__name__)

OnApplied = Callable[[], None]
DownloadAndVerify = Callable[..., Path]  # (update, *, dest_dir) -> artifact_path
Apply = Callable[..., None]  # (artifact_path, *, has_running_job) -> None

# How often to re-check `has_running_job()` while waiting for it to clear,
# both before invoking `on_applied` and (via `sleep_fn`) in tests.
_QUIESCENCE_POLL_INTERVAL_SECONDS = 0.5


def _default_on_applied() -> None:
    """Called only once `_run` has already confirmed, by polling
    `has_running_job`, that nothing is running -- see `UpdaterAdapter._run`.
    `apply_update()`'s own contract: "the caller is expected to shut down
    and exit" so its detached helper's wait-for-exit loop can proceed and
    relaunch the app (spec 11.4). A short delay lets the HTTP response
    carrying the "done" status actually reach the browser first.

    This is still a blunt `os._exit`, not `app/__main__.py`'s full
    graceful `shutdown()` (releasing the single-instance lock, stopping
    the watcher/sweeper too) -- production wiring that has a reference to
    those, and to the real `JobWorker` for a true
    `worker.stop(timeout=None)`, should construct `UpdaterAdapter` with
    its own `on_applied` instead of relying on this default.
    """

    def _exit() -> None:
        os._exit(0)

    threading.Timer(0.5, _exit).start()


_LIVE_APPLY_STATUSES = frozenset(
    {UpdateApplyStatus.PENDING, UpdateApplyStatus.DOWNLOADING, UpdateApplyStatus.APPLYING}
)


class UpdaterAdapter:
    """Implements `UpdateApplier` over `ash_captions.app.updater`.

    `download_and_verify`/`apply`/`sleep_fn` are injectable so tests can
    exercise job bookkeeping, error handling, and -- critically -- the
    wait-for-quiescence step below with no network, no zip extraction, no
    detached helper, and no real sleeping. Production defaults are the
    only place any of those are real.
    """

    def __init__(
        self,
        *,
        dest_dir: Path | None = None,
        on_applied: OnApplied = _default_on_applied,
        sleep_fn: Callable[[float], None] = time.sleep,
        download_and_verify: DownloadAndVerify = download_and_verify_update,
        apply: Apply = apply_update,
    ) -> None:
        self._dest_dir = Path(dest_dir) if dest_dir is not None else data_root() / "updates"
        self._on_applied = on_applied
        self._sleep_fn = sleep_fn
        self._download_and_verify = download_and_verify
        self._apply = apply
        self._lock = threading.Lock()
        self._jobs: dict[str, UpdateApplyJob] = {}

    def submit_apply(self, update: Any, *, has_running_job: Callable[[], bool]) -> UpdateApplyJob:
        job_id = uuid.uuid4().hex
        job = UpdateApplyJob(id=job_id, status=UpdateApplyStatus.PENDING)
        with self._lock:
            # Single-flight: two clicks (or two tabs) must not race the same
            # download destination, staging directory and helper script.
            for existing in self._jobs.values():
                if existing.status in _LIVE_APPLY_STATUSES:
                    raise UpdateApplyBusyError(existing.id)
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run,
            args=(job_id, update, has_running_job),
            daemon=True,
            name=f"ash-captions-update-{job_id[:8]}",
        )
        thread.start()
        return job

    def get_apply_status(self, job_id: str) -> UpdateApplyJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise UpdateApplyNotFoundError(job_id)
        return job

    def _set(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id] = self._jobs[job_id].model_copy(update=fields)

    def _run(self, job_id: str, update: Any, has_running_job: Callable[[], bool]) -> None:
        try:
            self._set(job_id, status=UpdateApplyStatus.DOWNLOADING)
            artifact_path = self._download_and_verify(update, dest_dir=self._dest_dir)

            self._set(job_id, status=UpdateApplyStatus.APPLYING)
            self._apply(artifact_path, has_running_job=has_running_job)

            # apply_update() checks has_running_job() twice internally but
            # its own docstring is explicit about the residual window: a
            # job could still start in the instant between its second
            # check and this process actually exiting, and closing that
            # is the caller's job, normally via a blocking, unbounded
            # `JobWorker.stop(timeout=None)` before exiting -- not the
            # bounded wait a normal Quit uses. This module has no
            # reference to the real JobWorker, so it polls the same
            # has_running_job() instead, with no cap -- genuinely
            # unbounded, the same semantics, just implemented from the
            # one signal reachable here. `on_applied` (which is what
            # actually exits the process) never fires while this is true.
            while has_running_job():
                self._sleep_fn(_QUIESCENCE_POLL_INTERVAL_SECONDS)

            self._set(job_id, status=UpdateApplyStatus.DONE)
            self._on_applied()
        except UpdateApplyError as exc:
            # Covers a download/verification failure and (per
            # app.updater's own required guard) an update refused because
            # a caption job was running at either of apply_update()'s two
            # checks -- str(exc) is already a plain, editor-facing
            # sentence in both cases (JOB_RUNNING_MESSAGE for the latter),
            # never a stack trace.
            logger.warning("update apply %s refused/failed: %s", job_id, exc)
            self._set(job_id, status=UpdateApplyStatus.FAILED, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - any failure must reach the browser as a status, never crash the thread
            logger.exception("update apply %s failed unexpectedly", job_id)
            self._set(job_id, status=UpdateApplyStatus.FAILED, error=str(exc))
