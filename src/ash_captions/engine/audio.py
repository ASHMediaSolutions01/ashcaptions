"""Audio extraction from video files via ffmpeg.

Converts any ffmpeg-readable video into a 16kHz mono WAV suitable for
faster-whisper. ffmpeg is never assumed to be on PATH -- callers must
supply the full path to ffmpeg.exe (default: ``bin/ffmpeg.exe`` relative
to the app root, per spec 11.1).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .ffmpeg_process import no_window_flags

DEFAULT_FFMPEG_PATH = Path("bin/ffmpeg.exe")

TARGET_SAMPLE_RATE_HZ = 16_000
TARGET_CHANNELS = 1


class AudioExtractionError(Exception):
    """Raised when audio cannot be extracted from a video file.

    ``stderr`` carries ffmpeg's own error output (when ffmpeg ran at all)
    so the caller can surface something more useful than a bare exit code.
    """

    def __init__(self, message: str, *, stderr: str = "", returncode: int | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def extract_audio(
    video_path: Path | str,
    output_path: Path | str,
    *,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
) -> Path:
    """Extract a 16kHz mono PCM WAV audio track from a video file.

    Args:
        video_path: source video. Any container/codec ffmpeg reads (MP4,
            MOV, MKV, MXF, ...). Paths with spaces or non-ASCII characters
            are handled -- arguments are passed as a list, never through a
            shell, so no manual quoting or escaping is needed.
        output_path: destination ``.wav`` path. Parent directories are
            created if missing.
        ffmpeg_path: full path to ``ffmpeg.exe``. Never assumed to be on
            PATH (spec 11.1).

    Returns:
        ``output_path``, unchanged, for convenient chaining.

    Raises:
        AudioExtractionError: the input video is missing, ffmpeg cannot be
            launched (e.g. wrong path), or ffmpeg exits non-zero.
    """
    video_path = Path(video_path)
    output_path = Path(output_path)
    ffmpeg_path = Path(ffmpeg_path)

    if not video_path.is_file():
        raise AudioExtractionError(f"Input video not found: {video_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        str(ffmpeg_path),
        "-y",
        "-hide_banner",
        # No stdin: ffmpeg otherwise polls the console for 'q', and a tray
        # app has no console to give it. No stats: an hour of progress
        # lines is a lot of stderr to buffer for nothing.
        "-nostdin",
        "-loglevel", "error",
        "-nostats",
        "-i", str(video_path),
        # The first audio stream only. A camera file with two audio tracks
        # (mic + ambient) would otherwise have them mixed by ffmpeg's
        # default stream selection rules, which pick "best", not "first".
        "-map", "0:a:0",
        "-vn",
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_SAMPLE_RATE_HZ),
        "-acodec", "pcm_s16le",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window_flags(),
        )
    except FileNotFoundError as exc:
        raise AudioExtractionError(
            f"ffmpeg executable not found at {ffmpeg_path}", stderr=str(exc)
        ) from exc
    except OSError as exc:
        raise AudioExtractionError(
            f"Failed to launch ffmpeg at {ffmpeg_path}: {exc}", stderr=str(exc)
        ) from exc

    if result.returncode != 0:
        raise AudioExtractionError(
            f"ffmpeg failed extracting audio from {video_path.name} "
            f"(exit {result.returncode})",
            stderr=result.stderr,
            returncode=result.returncode,
        )

    return output_path
