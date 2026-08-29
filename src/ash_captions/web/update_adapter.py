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
import uuid
from pathlib import Path
from typing import Any, Callable

from ash_captions.app.updater import UpdateApplyError, apply_update, download_and_verify_update
from ash_captions.config import data_root

from .interfaces import UpdateApplyNotFoundError
from .models import UpdateApplyJob, UpdateApplyStatus

logger = logging.getLogger(__name__)

OnApplied = Callable[[], None]


def _default_on_applied() -> None:
    """`apply_update()`'s own contract: "the caller is expected to shut
    down and exit" so its detached helper's wait-for-exit loop can proceed
    and relaunch the app (spec 11.4). A short delay lets the HTTP response
    carrying the "done" status actually reach the browser first.

    This is a blunt fallback, not a graceful shutdown -- it does not stop
    the watcher/worker/sweeper threads cleanly the way `app/__main__.py`'s
    own `shutdown()` closure does. Production wiring that wants a clean
    stop should construct `UpdaterAdapter(on_applied=<that closure>)`
    itself and pass it into `create_app(..., update_applier=...)` instead
    of relying on this default.
    """

    def _exit() -> None:
        os._exit(0)

    threading.Timer(1.5, _exit).start()


class UpdaterAdapter:
    """Implements `UpdateApplier` over `ash_captions.app.updater`."""

    def __init__(self, *, dest_dir: Path | None = None, on_applied: OnApplied = _default_on_applied) -> None:
        self._dest_dir = Path(dest_dir) if dest_dir is not None else data_root() / "updates"
        self._on_applied = on_applied
        self._lock = threading.Lock()
        self._jobs: dict[str, UpdateApplyJob] = {}

    def submit_apply(self, update: Any) -> UpdateApplyJob:
        job_id = uuid.uuid4().hex
        job = UpdateApplyJob(id=job_id, status=UpdateApplyStatus.PENDING)
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run,
            args=(job_id, update),
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

    def _run(self, job_id: str, update: Any) -> None:
        try:
            self._set(job_id, status=UpdateApplyStatus.DOWNLOADING)
            artifact_path = download_and_verify_update(update, dest_dir=self._dest_dir)

            self._set(job_id, status=UpdateApplyStatus.APPLYING)
            apply_update(artifact_path)

            self._set(job_id, status=UpdateApplyStatus.DONE)
            self._on_applied()
        except UpdateApplyError as exc:
            # Covers both a download/verification failure and (per
            # app.updater's own module-level guard) an update refused
            # because a caption job started running after our own
            # pre-check in app.py -- str(exc) is already a plain,
            # editor-facing sentence in both cases, never a stack trace.
            logger.warning("update apply %s refused/failed: %s", job_id, exc)
            self._set(job_id, status=UpdateApplyStatus.FAILED, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - any failure must reach the browser as a status, never crash the thread
            logger.exception("update apply %s failed unexpectedly", job_id)
            self._set(job_id, status=UpdateApplyStatus.FAILED, error=str(exc))
