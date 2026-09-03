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

Memory across restarts
----------------------
``_enqueued`` is what stops a file being reported twice. It starts empty
per process, so ``start()`` seeds it from ``known_paths`` -- the caller
passes every non-done job's input path from the database -- otherwise a
restart re-reports every file still sitting in ``in\\``. It also shrinks:
a path that disappears from the listing is forgotten, so a second drop of
the same filename later is picked up without a restart (the database's
one-live-job-per-input rule is the backstop if it reappears too fast).
"""

from __future__ import annotations

import ctypes
import logging
import os
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger("ash_captions.pipeline.watcher")

# Anything ffmpeg reads that we expect to receive (spec section 10).
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".mxf", ".avi", ".m4v", ".webm", ".wmv",
}

# Partial-download / in-progress-copy markers. A file with one of these
# suffixes is never eligible, no matter how long it sits still.
IGNORED_EXTENSIONS = {".tmp", ".part", ".crdownload"}

# A file stable but still "locked" for this long gets one warning line --
# long enough not to fire during a normal copy's tail, short enough that
# an editor waiting on a drop finds the explanation in the log.
LOCKED_WARNING_AFTER_SECONDS = 60.0

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33


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


def _is_readonly(path: Path) -> bool:
    try:
        attributes = os.stat(path).st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_READONLY)


def _probe_share_exclusive(path: Path) -> bool:
    """Win32 ``CreateFileW`` with share mode 0: succeeds only if nobody else
    holds a handle. The only way to ask "is the copy finished?" about a
    read-only file, which ``open(path, 'rb+')`` refuses forever.
    """
    if sys.platform != "win32":  # pragma: no cover - Windows-only tool
        with open(path, "rb"):
            return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    )
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    handle = kernel32.CreateFileW(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        error = ctypes.get_last_error()
        if error in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION):
            return False
        if error in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            raise FileNotFoundError(error, "file vanished during share probe", str(path))
        raise ctypes.WinError(error)
    kernel32.CloseHandle(handle)
    return True


def _default_exclusive_open(path: Path) -> bool:
    """Confirm no other process holds a write handle on ``path``.

    Opening for update ('rb+') requires GENERIC_WRITE access; on Windows
    this raises PermissionError if a copy tool still has the file open
    without shared write access -- exactly the "OS still holds a handle"
    case from the spec. A file carrying the read-only attribute (common
    straight off a camera card) can never be opened 'rb+', so it is probed
    with a share-nothing ``CreateFileW`` instead. Returns False (retry
    later) when the file is genuinely held, and re-raises anything else
    (e.g. the file vanished).
    """
    try:
        with open(path, "rb+"):
            return True
    except PermissionError:
        if _is_readonly(path):
            return _probe_share_exclusive(path)
        return False


class Watcher:
    """Watches a directory for finished video drops and reports them.

    ``on_ready`` is called once per file, exactly once, after it has passed
    the stability check and the exclusive-open confirmation. It is the
    caller's job to actually enqueue the job (this module has no DB
    dependency, by design -- see the pipeline queue module for that);
    ``known_paths`` is how the caller lends it the database's memory at
    ``start()`` (see module docstring).
    """

    def __init__(
        self,
        watch_dir: str | Path,
        on_ready: Callable[[Path], None],
        *,
        stable_checks_required: int = 3,
        check_interval: float = 1.5,
        open_check: Callable[[Path], bool] = _default_exclusive_open,
        known_paths: Callable[[], Iterable[Path]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if check_interval <= 0:
            raise ValueError("check_interval must be positive")
        self.watch_dir = Path(watch_dir)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self._on_ready = on_ready
        self._check_interval = check_interval
        self._open_check = open_check
        self._known_paths = known_paths
        self._clock = clock
        self._tracker = StabilityTracker(stable_checks_required)
        self._enqueued: set[Path] = set()
        self._first_seen: dict[Path, float] = {}
        self._locked_since: dict[Path, float] = {}
        self._locked_warned: set[Path] = set()
        # Wall-clock time of the last completed tick, for the health line.
        self.last_poll_at: float | None = None

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

    def seed_enqueued(self, paths: Iterable[Path]) -> int:
        """Treat ``paths`` (inside the watch dir) as already reported.
        Returns how many were added."""
        added = 0
        for raw in paths:
            path = Path(raw)
            if path.parent != self.watch_dir and path.parent.resolve() != self.watch_dir.resolve():
                continue
            if path not in self._enqueued:
                self._enqueued.add(path)
                added += 1
        return added

    def enqueued_paths(self) -> set[Path]:
        return set(self._enqueued)

    def poll_once(self) -> list[Path]:
        """Run one detection tick. Returns files newly handed to ``on_ready``.

        Safe to call directly and repeatedly (e.g. from tests) without
        starting the background thread or the watchdog observer.
        """
        newly_ready: list[Path] = []
        now = self._clock()

        seen: set[Path] = set()
        listing_ok = True
        try:
            candidates = list(self.watch_dir.iterdir())
        except OSError:
            # Directory unreachable (network share dropped, folder removed).
            # Nothing to do this tick -- and nothing to forget, since the
            # files may all still be there.
            candidates = []
            listing_ok = False

        for path in candidates:
            if not path.is_file() or not is_eligible(path):
                continue
            seen.add(path)
            if path in self._enqueued:
                continue
            try:
                st = path.stat()
            except OSError:
                # Vanished between listing and stat-ing (rename, deletion).
                self._forget(path)
                continue

            if path not in self._first_seen:
                self._first_seen[path] = now
                log.info("watch: seen %s (%d bytes); waiting for it to settle", path.name, st.st_size)

            stable = self._tracker.observe(path, st.st_size, st.st_mtime)
            if not stable:
                continue

            try:
                confirmed = self._open_check(path)
            except OSError:
                # e.g. the file vanished between stat() and the open
                # attempt. Drop it; it will reappear fresh if re-created.
                self._forget(path)
                continue

            if confirmed:
                self._enqueued.add(path)
                self._forget(path)
                newly_ready.append(path)
                log.info("watch: enqueued %s", path.name)
                self._on_ready(path)
            else:
                self._note_locked(path, now)
            # Still locked by another process: leave tracked; the
            # size/mtime streak is preserved and we simply retry the
            # exclusive-open confirmation on the next tick.

        if listing_ok:
            # Drop tracking state for anything that disappeared from the
            # directory listing entirely (deleted before ever stabilising,
            # or consumed by a finished job) -- including its "already
            # enqueued" memory, so the same filename can be dropped again.
            for stale in self._tracker.tracked_paths():
                if stale not in seen:
                    self._forget(stale)
            self._enqueued.intersection_update(seen)

        self.last_poll_at = time.time()
        return newly_ready

    def _forget(self, path: Path) -> None:
        self._tracker.forget(path)
        self._first_seen.pop(path, None)
        self._locked_since.pop(path, None)
        self._locked_warned.discard(path)

    def _note_locked(self, path: Path, now: float) -> None:
        since = self._locked_since.setdefault(path, now)
        if since == now:
            log.info("watch: %s is stable but still held open by another program; waiting", path.name)
        elif now - since >= LOCKED_WARNING_AFTER_SECONDS and path not in self._locked_warned:
            self._locked_warned.add(path)
            log.warning(
                "watch: %s has been locked by another program for over %d s -- "
                "close whatever is holding it (a copy tool, a media player) and it will be picked up",
                path.name, int(LOCKED_WARNING_AFTER_SECONDS),
            )

    def start(self) -> None:
        """Seed the already-queued set, then start the watchdog observer
        and the polling/stability thread."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Watcher is already running")

            if self._known_paths is not None:
                try:
                    seeded = self.seed_enqueued(self._known_paths())
                except Exception:  # noqa: BLE001 - a seeding failure must not stop the watcher
                    log.exception("watch: could not seed already-queued files; duplicates are still refused by the queue")
                else:
                    if seeded:
                        log.info("watch: %d file(s) in %s already queued from a previous run", seeded, self.watch_dir)

            self._stop_event.clear()
            self._wake_event.clear()

            observer = Observer()
            try:
                observer.schedule(
                    _WakeOnAnyEvent(self._wake_event), str(self.watch_dir), recursive=False
                )
                observer.start()
            except Exception:
                # Don't leave a half-started observer registered if
                # scheduling or starting it failed partway through.
                observer.stop()
                raise

            thread = threading.Thread(
                target=self._loop, name="ash-captions-watcher", daemon=True
            )
            # Only now, with both pieces successfully created, publish them
            # -- stop() (from any thread) can only ever see a fully-formed
            # observer/thread pair, never a half-built one.
            self._observer = observer
            self._thread = thread
            thread.start()

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop the observer and the polling thread.

        Idempotent and safe to call from any thread, any number of times,
        including when the watcher was never started. The lock+swap below
        ensures at most one caller ever ends up holding a given
        observer/thread pair to tear down: whichever call acquires the lock
        first atomically claims them and nulls the fields, so a second,
        concurrent call (or a later, sequential one) sees ``None`` and
        returns immediately instead of touching an Observer another thread
        is already stopping.
        """
        with self._lifecycle_lock:
            observer, self._observer = self._observer, None
            thread, self._thread = self._thread, None

        self._stop_event.set()
        self._wake_event.set()

        if observer is not None:
            observer.stop()
            observer.join(timeout=timeout)
        if thread is not None:
            thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - one bad tick must not end the watcher
                log.exception("watch: poll failed; retrying next tick")
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
