"""Run ffmpeg as a child process the app can see and stop.

Everything about *running* ffmpeg lives here so ``burn.py`` can stay
about *what* to run. Three things matter for an hour-long burn:

* **Progress and cancellation.** ffmpeg's ``-progress pipe:1`` output is
  parsed line by line; ``should_stop`` is checked on every line, so an
  editor pressing Stop waits at most one progress interval (~0.5s).
* **No orphans.** The process is killed in a ``finally`` if it is still
  alive when we leave, and every process started here is kept in a
  registry so the app can kill a running burn on tray Quit
  (``kill_active_processes()``) instead of leaving ffmpeg writing to a
  file the user thinks is finished.
* **No console windows.** The app runs from the tray; on Windows every
  child gets ``CREATE_NO_WINDOW`` (``no_window_flags()``), which the
  other engine modules reuse for ffprobe and nvidia-smi.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ProgressCallback = Callable[[float], None]  # called with a 0-100 percentage
StopCheck = Callable[[], bool]


def no_window_flags() -> dict[str, int]:
    """``subprocess`` keyword arguments that hide the child's console on
    Windows. Empty elsewhere, so call sites can always ``**`` it."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_active: set[subprocess.Popen] = set()
_active_lock = threading.Lock()


def _register(process: subprocess.Popen) -> None:
    with _active_lock:
        _active.add(process)


def _unregister(process: subprocess.Popen) -> None:
    with _active_lock:
        _active.discard(process)


def active_processes() -> list[subprocess.Popen]:
    """ffmpeg processes started by this module that have not exited yet."""
    with _active_lock:
        return [p for p in _active if p.poll() is None]


def kill_active_processes(*, timeout: float = 10.0) -> int:
    """Kill every running ffmpeg started by this module; returns how many.

    For the app's Quit path: a burn that is still running must not be
    left encoding into a ``.part`` file after the tray icon is gone. The
    burn that owned each process sees the kill as a non-zero exit and
    cleans up its own part file.
    """
    killed = 0
    for process in active_processes():
        _kill(process, timeout=timeout)
        killed += 1
    return killed


def _kill(process: subprocess.Popen, *, timeout: float = 10.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

_PROGRESS_TIME_US_RE = re.compile(r"^out_time_us=(\d+)$")
_PROGRESS_TIME_MS_RE = re.compile(r"^out_time_ms=(\d+)$")


def _parse_progress_line(line: str, duration_seconds: float) -> float | None:
    """Parse one line of ffmpeg ``-progress`` output into a 0-100 percentage.

    Returns None for lines that aren't a recognized time field, or when
    ``duration_seconds`` isn't usable (<= 0).
    """
    line = line.strip()
    match = _PROGRESS_TIME_US_RE.match(line) or _PROGRESS_TIME_MS_RE.match(line)
    if not match or duration_seconds <= 0:
        return None
    out_time_seconds = int(match.group(1)) / 1_000_000
    percentage = (out_time_seconds / duration_seconds) * 100
    return max(0.0, min(percentage, 100.0))


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FfmpegRun:
    """Outcome of one ffmpeg run. ``cancelled`` means ``should_stop`` fired
    and the process was killed; ``returncode`` is then whatever the kill
    produced and must not be read as ffmpeg's verdict."""

    returncode: int | None
    stderr: str
    cancelled: bool


def run_ffmpeg(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    duration_seconds: float = 0.0,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> FfmpegRun:
    """Run ``args`` (an ffmpeg argv using ``-progress pipe:1``) to completion.

    Raises ``OSError`` if the process cannot be launched; the caller turns
    that into its own error type. Never raises for a non-zero exit -- that
    is reported in the result so the caller can attach context.
    """
    process = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **no_window_flags(),
    )
    _register(process)

    # stderr MUST be drained concurrently, not after the stdout loop.
    # ffmpeg writes to both; if stderr's ~64KB pipe buffer fills it blocks,
    # which stops it emitting progress on stdout, which leaves the loop
    # below waiting forever. That deadlock hung every real burn-in -- it
    # stayed hidden because burn-in is off by default, so no job had
    # exercised this path against a genuine ffmpeg.
    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        if process.stderr is None:
            return
        try:
            for line in process.stderr:
                stderr_chunks.append(line)
        except (OSError, ValueError):
            pass  # pipe closed as the process exits -- nothing to report

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    cancelled = False
    try:
        if process.stdout is not None:
            for line in process.stdout:
                if should_stop is not None and should_stop():
                    cancelled = True
                    break
                percentage = _parse_progress_line(line, duration_seconds)
                if percentage is not None and on_progress is not None:
                    on_progress(percentage)
        if cancelled:
            _kill(process)
        returncode = process.wait()
    finally:
        # Whatever happened above -- a callback raising, a KeyboardInterrupt,
        # a broken pipe -- ffmpeg must not outlive this call.
        if process.poll() is None:
            _kill(process)
        _unregister(process)

    stderr_thread.join(timeout=10)
    for stream in (process.stdout, process.stderr):
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except (OSError, ValueError):
                pass
    return FfmpegRun(returncode=returncode, stderr="".join(stderr_chunks), cancelled=cancelled)
