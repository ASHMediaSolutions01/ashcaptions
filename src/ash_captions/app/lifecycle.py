"""Retention cleanup and logging setup (spec sections 10, 12).

Both pieces of app lifecycle that sit outside the request/response and
job-processing paths: rotating file logging, so editors can send a log
file instead of a console screenshot (there is no console -- spec section
11), and a periodic sweep that deletes output folders older than the
configured retention window. Neither is allowed to take the app down: a
failed cleanup pass is logged and skipped, never raised past this module.
"""

from __future__ import annotations

import logging
import logging.handlers
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# A 30-day retention window doesn't need checking every minute; four
# sweeps a day is plenty of margin without keeping a thread constantly busy.
DEFAULT_SWEEP_INTERVAL_SECONDS = 6 * 3600


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


def clean_old_outputs(
    out_dir: Path, *, retention_days: int, now: datetime | None = None
) -> list[Path]:
    """Delete output subfolders older than ``retention_days``.

    Age is judged by each folder's own mtime (touched whenever a job last
    wrote into it), not creation time -- Windows doesn't reliably track
    creation time across every filesystem. Returns the paths removed, for
    logging. Never raises: a folder that can't be inspected or removed
    (e.g. a file an editor still has open) is skipped and the sweep
    continues, per spec section 12's "must never take the app down."
    """
    if retention_days <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    removed: list[Path] = []

    try:
        candidates = list(Path(out_dir).iterdir())
    except OSError:
        return []

    for entry in candidates:
        try:
            if not entry.is_dir():
                continue
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                shutil.rmtree(entry)
                removed.append(entry)
        except OSError:
            continue

    return removed


class RetentionSweeper:
    """Runs ``clean_old_outputs`` on a timer, on its own background thread
    -- same start/stop shape as ``pipeline.JobWorker`` and
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
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._out_dir = Path(out_dir)
        self._retention_days = retention_days
        self._interval_seconds = interval_seconds
        self._logger = logger or logging.getLogger("ash_captions.lifecycle")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> list[Path]:
        """Run a single sweep synchronously. Exposed separately from the
        background loop so tests can drive it without threads or real
        sleeps -- and so a bug in the sweep itself is caught here, never
        propagated to the caller (spec section 12).
        """
        try:
            removed = clean_old_outputs(self._out_dir, retention_days=self._retention_days)
        except Exception:  # noqa: BLE001 - a cleanup bug must never take the app down
            self._logger.exception("Retention sweep failed")
            return []
        if removed:
            self._logger.info("Retention sweep removed %d old output folder(s)", len(removed))
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
