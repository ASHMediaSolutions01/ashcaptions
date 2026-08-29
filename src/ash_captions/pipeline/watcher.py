"""Watch-folder logic for the ``in\\`` directory (spec section 8.2).

This is the component most likely to ship broken if handled naively:
watchdog fires several ``modified`` events while a large file is still
being copied, and there is no "copy finished" event. Dropping a 6 GB 4K
file must never start a half-written transcription.

Detection model
----------------
Every tick (``Watcher.poll_once``) does two things at once, which is also
how the "poll as a backstop" requirement is satisfied without a second,
separate timer: it lists the watch directory (catching files whose watchdog
creation event Windows dropped during a bulk copy) and it re-stats every
file already being tracked. A file is considered ready only once size and
mtime have been observed identical across three consecutive ticks
(``StabilityTracker``), and only then is an exclusive-open attempted to
confirm the OS has released its handle. ``poll_once`` takes no wall-clock
dependency itself, so tests drive it tick-by-tick with no real sleeping.
Only the background loop started by ``start()`` waits between ticks (a
short, injectable ``check_interval``); ``poll_once()`` itself never waits.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

# Anything ffmpeg reads that we expect to receive (spec section 10).
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".mxf", ".avi", ".m4v", ".webm", ".wmv",
}

# Partial-download / in-progress-copy markers. A file with one of these
# suffixes is never eligible, no matter how long it sits still.
IGNORED_EXTENSIONS = {".tmp", ".part", ".crdownload"}


def is_eligible(path: Path) -> bool:
    """True if ``path`` looks like a finished video drop, by extension alone.

    This is an allow-list on the final suffix, so partial-download names
    like ``clip.mp4.part`` or ``clip.mp4.crdownload`` are excluded for free
    -- their final suffix is never a video extension. ``IGNORED_EXTENSIONS``
    is still checked explicitly so the intent is not implicit.
    """
    suffix = path.suffix.lower()
    if suffix in IGNORED_EXTENSIONS:
        return False
    return suffix in VIDEO_EXTENSIONS


class StabilityTracker:
    """Pure, stateful "has this file stopped changing" tracker.

    Deliberately holds no clock or filesystem dependency: callers feed it a
    fresh (size, mtime) reading and it tells them how many consecutive
    matching observations have been made. This is what lets the readiness
    logic be tested without waiting real seconds.
    """

    def __init__(self, required_checks: int = 3) -> None:
        if required_checks < 1:
            raise ValueError("required_checks must be at least 1")
        self.required_checks = required_checks
        self._last: dict[Path, tuple[int, float]] = {}
        self._streak: dict[Path, int] = {}

    def observe(self, path: Path, size: int, mtime: float) -> bool:
        """Record one observation; return True once the file is stable."""
        current = (size, mtime)
        if self._last.get(path) == current:
            self._streak[path] = self._streak.get(path, 0) + 1
        else:
            self._streak[path] = 1
            self._last[path] = current
        return self._streak[path] >= self.required_checks

    def forget(self, path: Path) -> None:
        """Drop tracking state for a path (processed, removed, or vanished)."""
        self._last.pop(path, None)
        self._streak.pop(path, None)

    def tracked_paths(self) -> list[Path]:
        """Paths currently held in state (observed at least once, not yet forgotten)."""
        return list(self._last.keys())


def _default_exclusive_open(path: Path) -> bool:
    """Confirm no other process holds a write handle on ``path``.

    Opening for update ('rb+') requires GENERIC_WRITE access; on Windows
    this raises PermissionError if a copy tool still has the file open
    without shared write access -- exactly the "OS still holds a handle"
    case from the spec. Returns False (retry later) on PermissionError,
    and re-raises anything else (e.g. the file vanished).
    """
    try:
        with open(path, "rb+"):
            return True
    except PermissionError:
        return False


class Watcher:
    """Watches a directory for finished video drops and reports them.

    ``on_ready`` is called once per file, exactly once, after it has passed
    the stability check and the exclusive-open confirmation. It is the
    caller's job to actually enqueue the job (this module has no DB
    dependency, by design -- see the pipeline queue module for that).
    """

    def __init__(
        self,
        watch_dir: str | Path,
        on_ready: Callable[[Path], None],
        *,
        stable_checks_required: int = 3,
        check_interval: float = 1.5,
        open_check: Callable[[Path], bool] = _default_exclusive_open,
    ) -> None:
        if check_interval <= 0:
            raise ValueError("check_interval must be positive")
        self.watch_dir = Path(watch_dir)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self._on_ready = on_ready
        self._check_interval = check_interval
        self._open_check = open_check
        self._tracker = StabilityTracker(stable_checks_required)
        self._ready_or_pending_open: set[Path] = set()
        self._enqueued: set[Path] = set()

        self._observer: Observer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        # Guards start()/stop() transitions. Without this, two threads
        # calling stop() concurrently can both read self._observer as
        # non-None before either clears it, and both end up operating on
        # the same watchdog Observer -- on Windows that has produced a
        # reproducible race (confirmed while diagnosing this) and, per
        # watchdog's own Windows backend, closes a native directory handle
        # from more than one place, which is unsafe.
        self._lifecycle_lock = threading.Lock()

    def poll_once(self) -> list[Path]:
        """Run one detection tick. Returns files newly handed to ``on_ready``.

        Safe to call directly and repeatedly (e.g. from tests) without
        starting the background thread or the watchdog observer.
        """
        newly_ready: list[Path] = []

        seen: set[Path] = set()
        try:
            candidates = list(self.watch_dir.iterdir())
        except FileNotFoundError:
            candidates = []

        for path in candidates:
            if not path.is_file() or not is_eligible(path) or path in self._enqueued:
                continue
            seen.add(path)
            try:
                st = path.stat()
            except OSError:
                # Vanished between listing and stat-ing (rename, deletion).
                self._tracker.forget(path)
                continue

            stable = self._tracker.observe(path, st.st_size, st.st_mtime)
            if not stable:
                continue

            try:
                confirmed = self._open_check(path)
            except OSError:
                # e.g. the file vanished between stat() and the open
                # attempt. Drop it; it will reappear fresh if re-created.
                self._tracker.forget(path)
                continue

            if confirmed:
                self._enqueued.add(path)
                self._tracker.forget(path)
                newly_ready.append(path)
                self._on_ready(path)
            # else: still locked by another process. Leave tracked; the
            # size/mtime streak is preserved and we simply retry the
            # exclusive-open confirmation on the next tick.

        # Drop tracking state for anything that disappeared from the
        # directory listing entirely (e.g. deleted before ever stabilising).
        for stale in self._tracker.tracked_paths():
            if stale not in seen and stale not in self._enqueued:
                self._tracker.forget(stale)

        return newly_ready

    def start(self) -> None:
        """Start the watchdog observer and the polling/stability thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Watcher is already running")

        handler = _WakeOnAnyEvent(self._wake_event)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.watch_dir), recursive=False)
        self._observer.start()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ash-captions-watcher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop the observer and the polling thread."""
        self._stop_event.set()
        self._wake_event.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            # Wake early on a watchdog event, but always re-poll at the
            # interval regardless -- events are a latency optimisation
            # here, not the source of truth. Windows drops some creation
            # events during bulk copies, so the directory listing in
            # poll_once() is the backstop that must not depend on them.
            self._wake_event.wait(timeout=self._check_interval)
            self._wake_event.clear()


class _WakeOnAnyEvent(FileSystemEventHandler):
    """Nudges the watcher's poll loop sooner on any filesystem activity."""

    def __init__(self, wake_event: threading.Event) -> None:
        self._wake_event = wake_event

    def on_any_event(self, event: FileSystemEvent) -> None:
        self._wake_event.set()
