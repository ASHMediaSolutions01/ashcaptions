"""Job thumbnails: one 320-px-wide JPEG per job, taken 10% into the source,
written once into the job's output folder as ``.thumb.jpg`` and served
from there ever after (``GET /api/jobs/{id}/thumb`` in ``routes_desktop.py``).

Everything here does real filesystem and ffmpeg work, so the route calls
``ensure_thumbnail`` through ``run_in_threadpool`` -- never on the event
loop. A per-folder lock stops two tabs asking for the same job's thumb
from running two ffmpegs into the same file; the write is ``.part`` +
rename so a killed ffmpeg never leaves a half-written thumb under the
final name (the same rule every pipeline output follows).

The source is the job's input footage; when that has gone (a watch-folder
job whose input was cleaned up) the burned output stands in. With neither
there is no thumb, and the route says 404.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from ash_captions.config import find_binary

THUMB_NAME = ".thumb.jpg"
THUMB_WIDTH = 320
THUMB_POSITION = 0.10  # fraction of the duration
THUMB_TIMEOUT_SECONDS = 60
# A 4K frame with a 320-px scale finishes in well under a second; the
# timeout only matters for a source on a dead network share.

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def thumbnail_path(output_dir: Path) -> Path:
    return Path(output_dir) / THUMB_NAME


def ensure_thumbnail(output_dir: Path, *sources: Path | str | None) -> Path | None:
    """The job's thumb, generating it from the first existing source when
    it is not there yet. Returns None when no source exists or ffmpeg
    could not produce a frame (a corrupt or audio-only file)."""
    output_dir = Path(output_dir)
    target = thumbnail_path(output_dir)
    if target.is_file():
        return target
    source = next((Path(s) for s in sources if s and Path(s).is_file()), None)
    if source is None:
        return None
    with _lock_for(output_dir):
        if target.is_file():  # another request finished it while we waited
            return target
        return _generate(source, target)


def _lock_for(output_dir: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(output_dir.resolve(), threading.Lock())


def _generate(source: Path, target: Path) -> Path | None:
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg is None:
        return None
    seek = THUMB_POSITION * (probe_duration(source) or 0.0)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{seek:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={THUMB_WIDTH}:-2",
        "-q:v",
        "4",
        "-f",
        "image2",
        str(part),
    ]
    try:
        completed = _run(command, THUMB_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        part.unlink(missing_ok=True)
        return None
    if completed.returncode != 0 or not part.is_file() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        return None
    part.replace(target)
    return target


def probe_duration(source: Path) -> float | None:
    """Seconds of media in ``source`` via ffprobe, or None when unknown."""
    ffprobe = find_binary("ffprobe")
    if ffprobe is None:
        return None
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(source),
    ]
    try:
        completed = _run(command, THUMB_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return float((completed.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _run(command: list[str], timeout: float):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
