"""Production implementation of web's `PreviewRenderer` protocol (spec
7A.3): the style editor's live ~3s preview.

This is the one place under `web/` that imports `ash_captions.engine`
directly, alongside `ash_captions.styles` (which is explicitly meant to be
consumed here -- see `styles_adapter.py`). `app.py` itself stays decoupled
from both via the `PreviewRenderer` protocol; this module is a production
implementation of that protocol, the same relationship `app.adapter.
QueueAdapter` has to `pipeline.JobStore`.

A meaningful preview needs real words for the seconds the editor picked, so
this extracts just that audio window, transcribes it, builds caption cards
the same way a real job would, renders the in-progress style to `.ass`, and
burns a short clip. Per `styles.preview`'s own docstring, seeking before
`-i` means each preview costs roughly the length of the clip itself, not
the length of the source video.

Runs off the request thread: whisper plus two ffmpeg passes easily take
several seconds, and spec 7A.3 depends on the request returning immediately
with a job handle the browser polls, never blocking. One background thread
per preview job -- previews are infrequent, single-editor, and ephemeral
(nothing here persists across a restart the way the real job queue does),
so unlike `pipeline.JobWorker` this doesn't need a persistent, single
worker queue.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from ash_captions.engine import Card, Transcriber, TranscriptionError, WhisperTranscriber, build_cards
from ash_captions.styles import (
    DEFAULT_PREVIEW_DURATION_SECONDS,
    StyleValidationError,
    build_preview_command,
    validate_style_dict,
    write_ass,
)

from .interfaces import PreviewNotFoundError, StyleValidationFailedError
from .models import PreviewJob, PreviewStatus

logger = logging.getLogger(__name__)

DEFAULT_FFMPEG_PATH = Path("bin/ffmpeg.exe")

# Extracts just the preview window as 16kHz mono PCM (what WhisperTranscriber
# wants). Signature: (video_path, out_wav_path, start_seconds, duration_seconds, ffmpeg_path).
ExtractWindowAudio = Callable[[Path, Path, float, float, Path], None]
# Runs an ffmpeg argv to completion; raises on failure.
RunFfmpeg = Callable[[list[str]], None]


def _default_extract_window_audio(
    video_path: Path, out_wav: Path, start_seconds: float, duration_seconds: float, ffmpeg_path: Path
) -> None:
    """`engine.audio.extract_audio` has no `-ss`/`-t` support (it always
    reads a whole file), so this is a small, deliberate duplicate of its
    ffmpeg invocation with a time window added, rather than a reach into a
    module this package doesn't own."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    args = [
        str(ffmpeg_path),
        "-y",
        "-ss", f"{start_seconds:.3f}",
        "-t", f"{duration_seconds:.3f}",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        str(out_wav),
    ]
    _run(args, what="extracting preview audio")


def _default_run_ffmpeg(args: list[str]) -> None:
    _run(args, what="rendering the preview clip")


def _run(args: list[str], *, what: str) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"Failed to launch ffmpeg while {what}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed while {what} (exit {result.returncode}): {result.stderr}")


class InProcessPreviewRenderer:
    """Implements `PreviewRenderer`. `transcriber`/`extract_window_audio`/
    `run_ffmpeg` are injectable so tests can exercise job bookkeeping and
    error handling with no ffmpeg, no whisper model, and no filesystem
    rendering -- the production defaults are the only place any of those
    are real."""

    def __init__(
        self,
        *,
        transcriber: Transcriber | None = None,
        ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
        work_dir: Path | None = None,
        extract_window_audio: ExtractWindowAudio = _default_extract_window_audio,
        run_ffmpeg: RunFfmpeg = _default_run_ffmpeg,
    ) -> None:
        self._transcriber = transcriber or WhisperTranscriber()
        self._ffmpeg_path = Path(ffmpeg_path)
        self._work_dir = Path(work_dir) if work_dir is not None else Path(tempfile.gettempdir()) / "ash-captions-previews"
        self._extract_window_audio = extract_window_audio
        self._run_ffmpeg = run_ffmpeg
        self._lock = threading.Lock()
        self._jobs: dict[str, PreviewJob] = {}

        # Keyed by (video_path, start_seconds): an editor flips through
        # styles at the same timestamp far more than they change the
        # timestamp itself, and transcription -- not rendering -- is the
        # slow, re-doable-for-nothing part of a preview. Re-using it across
        # style changes at the same spot is the single biggest thing that
        # makes flipping through styles feel fast (team-lead's note).
        self._cache_lock = threading.Lock()
        self._transcription_cache: dict[tuple[str, float], tuple[Card, ...]] = {}

    def submit_preview(self, video_path: Path, start_seconds: float, style: dict[str, Any]) -> PreviewJob:
        try:
            validated_style = validate_style_dict(style)
        except StyleValidationError as exc:
            raise StyleValidationFailedError(str(exc)) from exc

        job_id = uuid.uuid4().hex
        job = PreviewJob(id=job_id, status=PreviewStatus.PENDING)
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, Path(video_path), float(start_seconds), validated_style),
            daemon=True,
            name=f"ash-captions-preview-{job_id[:8]}",
        )
        thread.start()
        return job

    def get_preview(self, job_id: str) -> PreviewJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise PreviewNotFoundError(job_id)
        return job

    # -- background work -----------------------------------------------------

    def _set(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id] = self._jobs[job_id].model_copy(update=fields)

    def _run_job(self, job_id: str, video_path: Path, start_seconds: float, style: Any) -> None:
        job_dir = self._work_dir / job_id
        duration_seconds = DEFAULT_PREVIEW_DURATION_SECONDS
        try:
            job_dir.mkdir(parents=True, exist_ok=True)

            cards = self._cards_for_window(job_id, video_path, start_seconds, duration_seconds)

            self._set(job_id, status=PreviewStatus.RUNNING, phase="rendering")
            ass_path = job_dir / "preview.ass"
            write_ass(cards, ass_path, style)

            clip_path = job_dir / "preview.mp4"
            command = build_preview_command(
                video_path,
                ass_path,
                clip_path,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
                ffmpeg_path=self._ffmpeg_path,
            )
            self._run_ffmpeg(command)

            self._set(job_id, status=PreviewStatus.DONE, phase=None, clip_path=str(clip_path))
        except TranscriptionError as exc:
            logger.warning("preview %s: transcription failed: %s", job_id, exc)
            self._set(job_id, status=PreviewStatus.FAILED, phase=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - any failure must reach the browser as a status, never crash the thread
            logger.warning("preview %s: failed: %s", job_id, exc)
            self._set(job_id, status=PreviewStatus.FAILED, phase=None, error=str(exc))

    def _cards_for_window(
        self, job_id: str, video_path: Path, start_seconds: float, duration_seconds: float
    ) -> tuple[Card, ...]:
        cache_key = (str(video_path), round(start_seconds, 3))
        with self._cache_lock:
            cached = self._transcription_cache.get(cache_key)
        if cached is not None:
            return cached

        self._set(job_id, status=PreviewStatus.RUNNING, phase="transcribing")
        job_dir = self._work_dir / job_id
        wav_path = job_dir / "window.wav"
        self._extract_window_audio(video_path, wav_path, start_seconds, duration_seconds, self._ffmpeg_path)

        result = self._transcriber.transcribe(wav_path)
        cards = tuple(build_cards(list(result.words)))

        with self._cache_lock:
            self._transcription_cache[cache_key] = cards
        return cards
