"""Retention cleanup and logging setup (spec sections 10, 12).

Both pieces of app lifecycle that sit outside the request/response and
job-processing paths: rotating file logging, so editors can send a log
file instead of a console screenshot (there is no console -- spec section
11), and a periodic sweep that deletes output folders older than the
configured retention window. Neither is allowed to take the app down: a
failed cleanup pass is logged and skipped, never raised past this module.

What the sweep may delete
-------------------------
Only folders this app created: each job writes a ``.ash-captions-job``
marker (holding its job id) into its output folder at start, and the
sweep deletes nothing without one. A folder whose job is still pending or
running is skipped (``folder_is_live``), age is the newer of the folder's
and the marker's mtime (NTFS does not bump a folder's mtime when a file
inside is rewritten, so a re-run into an old folder would otherwise be
swept the same night), and an ``out_dir`` that is a drive root or holds
no marker at all is refused outright.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("ash_captions.lifecycle")

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# A 30-day retention window doesn't need checking every minute; four
# sweeps a day is plenty of margin without keeping a thread constantly busy.
DEFAULT_SWEEP_INTERVAL_SECONDS = 6 * 3600
# Browser uploads are copies we made; a day after the job is over they are
# just wasted disk.
DEFAULT_UPLOAD_MAX_AGE_DAYS = 1

MARKER_FILENAME = ".ash-captions-job"

FolderIsLive = Callable[[Path], bool]


def configure_logging(log_path: Path, *, level: int = logging.INFO) -> logging.Logger:
    """Attach rotating file logging to the root logger, so third-party
    library logs (uvicorn, watchdog) land in the same file as our own, and
    return the ``ash_captions`` logger for callers to log through.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    return logging.getLogger("ash_captions")


def write_job_marker(output_dir: Path, job_id: int) -> Path:
    """Stamp ``output_dir`` as owned by job ``job_id`` (see module docstring)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    marker = output_dir / MARKER_FILENAME
    marker.write_text(f"{job_id}\n", encoding="utf-8")
    return marker


def _is_drive_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved.parent == resolved


def _within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def folder_is_live(store: Any, folder: Path) -> bool:
    """True if any ``pending``/``running`` job writes to or reads from
    ``folder``. ``store`` only needs ``list_live_jobs()``."""
    folder = Path(folder)
    for job in store.list_live_jobs():
        if Path(job.output_dir) == folder or _within(Path(job.output_dir), folder):
            return True
        if _within(Path(job.input_path), folder):
            return True
    return False


def _older_than(entry: Path, cutoff: datetime, *, marker: Path | None = None) -> bool:
    newest = entry.stat().st_mtime
    if marker is not None:
        newest = max(newest, marker.stat().st_mtime)
    return datetime.fromtimestamp(newest, tz=timezone.utc) < cutoff


def clean_old_outputs(
    out_dir: Path,
    *,
    retention_days: int,
    now: datetime | None = None,
    folder_is_live: FolderIsLive | None = None,
) -> list[Path]:
    """Delete marked output subfolders older than ``retention_days``.

    Returns the paths removed, for logging. Never raises: a folder that
    can't be inspected or removed (e.g. a file an editor still has open)
    is skipped and the sweep continues, per spec section 12's "must never
    take the app down." See the module docstring for the ownership rules.
    """
    if retention_days <= 0:
        return []
    out_dir = Path(out_dir)
    try:
        if _is_drive_root(out_dir):
            log.warning("Retention sweep refused: %s is a drive root", out_dir)
            return []
        candidates = list(out_dir.iterdir())
    except OSError:
        return []

    marked: list[tuple[Path, Path]] = []
    for entry in candidates:
        try:
            marker = entry / MARKER_FILENAME
            if entry.is_dir() and marker.is_file():
                marked.append((entry, marker))
        except OSError:
            continue
    if not marked:
        return []  # nothing here is ours to delete

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    removed: list[Path] = []
    for entry, marker in marked:
        try:
            if folder_is_live is not None and folder_is_live(entry):
                continue
            if _older_than(entry, cutoff, marker=marker):
                shutil.rmtree(entry)
                removed.append(entry)
        except OSError:
            continue
    return removed


def clean_old_uploads(
    upload_dir: Path,
    *,
    max_age_days: int = DEFAULT_UPLOAD_MAX_AGE_DAYS,
    now: datetime | None = None,
    folder_is_live: FolderIsLive | None = None,
) -> list[Path]:
    """Delete per-upload subfolders older than ``max_age_days`` whose job
    is no longer live. ``upload_dir`` is entirely ours (the control page's
    upload route creates one ``<uuid>/`` folder per file), so no marker is
    needed -- but the drive-root refusal still applies."""
    if max_age_days <= 0:
        return []
    upload_dir = Path(upload_dir)
    try:
        if _is_drive_root(upload_dir):
            log.warning("Upload sweep refused: %s is a drive root", upload_dir)
            return []
        candidates = list(upload_dir.iterdir())
    except OSError:
        return []

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    removed: list[Path] = []
    for entry in candidates:
        try:
            if not entry.is_dir():
                continue
            if folder_is_live is not None and folder_is_live(entry):
                continue
            if _older_than(entry, cutoff):
                shutil.rmtree(entry)
                removed.append(entry)
        except OSError:
            continue
    return removed


_SCRATCH_ENTRY_RE = re.compile(r"^(job-\d+|ash-(burn|matte|preview)-.*|.*\.part)$")


def sweep_tmp_dir(tmp_dir: Path) -> int:
    """Remove everything inside the per-job scratch directory. Called at
    startup, before the worker starts, so anything there is a leftover
    from a job that was killed mid-extract. Returns the entry count removed."""
    tmp_dir = Path(tmp_dir)
    if _is_drive_root(tmp_dir):
        log.error("refusing to sweep %s: it is a drive root (check tmp_dir in settings.json)", tmp_dir)
        return 0
    try:
        entries = list(tmp_dir.iterdir())
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        # Only our own scratch entries (job-<id>, matte/burn staging). A
        # mistyped tmp_dir in settings.json must not empty someone's folder.
        if not _SCRATCH_ENTRY_RE.match(entry.name):
            log.warning("leaving %s alone: not a scratch entry this app created", entry)
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            log.warning("could not remove leftover scratch entry %s", entry)
    if removed:
        log.info("removed %d leftover scratch entr%s from %s", removed, "y" if removed == 1 else "ies", tmp_dir)
    return removed


class RetentionSweeper:
    """Runs ``clean_old_outputs`` (and, when given an ``upload_dir``,
    ``clean_old_uploads``) on a timer, on its own background thread --
    same start/stop shape as ``pipeline.JobWorker`` and
    ``pipeline.Watcher``, for the same reason: testable without real
    sleeps -- ``run_once()`` drives a single pass directly, and the
    background loop waits on an ``Event`` (interruptible by ``stop()``)
    rather than a plain ``time.sleep``.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        retention_days: int,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
        logger: logging.Logger | None = None,
        upload_dir: Path | None = None,
        upload_max_age_days: int = DEFAULT_UPLOAD_MAX_AGE_DAYS,
        folder_is_live: FolderIsLive | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._out_dir = Path(out_dir)
        self._retention_days = retention_days
        self._interval_seconds = interval_seconds
        self._logger = logger or log
        self._upload_dir = Path(upload_dir) if upload_dir is not None else None
        self._upload_max_age_days = upload_max_age_days
        self._folder_is_live = folder_is_live
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> list[Path]:
        """Run a single sweep synchronously. Exposed separately from the
        background loop so tests can drive it without threads or real
        sleeps -- and so a bug in the sweep itself is caught here, never
        propagated to the caller (spec section 12).
        """
        removed: list[Path] = []
        try:
            removed += clean_old_outputs(
                self._out_dir,
                retention_days=self._retention_days,
                folder_is_live=self._folder_is_live,
            )
            if self._upload_dir is not None:
                removed += clean_old_uploads(
                    self._upload_dir,
                    max_age_days=self._upload_max_age_days,
                    folder_is_live=self._folder_is_live,
                )
        except Exception:  # noqa: BLE001 - a cleanup bug must never take the app down
            self._logger.exception("Retention sweep failed")
            return removed
        if removed:
            self._logger.info("Retention sweep removed %d old folder(s)", len(removed))
        return removed

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("RetentionSweeper is already running")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ash-captions-retention", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(timeout=self._interval_seconds)
