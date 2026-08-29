"""Burn captions into video via ffmpeg, using the .ass file as a subtitle filter.

Optional step (spec 8, 10) producing ``*.captioned.mp4``. Uses NVENC when
the machine has an NVIDIA GPU (``nvidia-smi`` presence, per spec 11.2),
falling back to libx264 on CPU-only machines. Progress is reported by
parsing ffmpeg's ``-progress`` output into a percentage; that requires
knowing the video's total duration up front, which this module does not
probe itself -- the caller supplies it (e.g. from an earlier ffprobe call
or from the transcription result).

Bundled fonts (spec 7A.4) only resolve if libass is told where to find
them: both ``build_burn_command`` and ``burn_captions`` take an optional
``fontsdir``, which the caller supplies (typically
``ash_captions.styles.fontsdir_arg()``) -- this module doesn't assume it,
to keep the engine package decoupled from the styles package.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .audio import DEFAULT_FFMPEG_PATH

DEFAULT_NVIDIA_SMI_PATH = "nvidia-smi"

ProgressCallback = Callable[[float], None]  # called with a 0-100 percentage


class BurnInError(Exception):
    """Raised when captions cannot be burned into the video."""

    def __init__(self, message: str, *, stderr: str = "", returncode: int | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def detect_nvenc(*, nvidia_smi_path: str = DEFAULT_NVIDIA_SMI_PATH) -> bool:
    """Return True if an NVIDIA GPU (and thus NVENC) looks available.

    Per spec 11.2, ``nvidia-smi`` presence is a reliable signal -- it ships
    with the display driver, not only the CUDA toolkit.
    """
    try:
        result = subprocess.run(
            [nvidia_smi_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def build_burn_command(
    video_path: Path | str,
    ass_path: Path | str,
    output_path: Path | str,
    *,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    use_nvenc: bool = False,
    fontsdir: Path | str | None = None,
) -> list[str]:
    """Construct the ffmpeg argv that burns ``ass_path`` into ``video_path``.

    ``fontsdir`` points libass at the bundled font directory (spec 7A.4:
    ``ash_captions.styles.fontsdir_arg()``) so a style referencing one of
    the ~24 bundled faces actually resolves to it, instead of falling
    back to whatever's installed system-wide -- silently, since libass
    substitutes rather than erroring. Left ``None`` (the default), the
    filter is unchanged from before this parameter existed, byte for
    byte: this module doesn't assume a fonts directory, and it's the
    caller's job to pass one (see ``ash_captions.styles.fontsdir_arg``).
    """
    video_path = Path(video_path)
    ass_path = Path(ass_path)
    output_path = Path(output_path)

    subtitle_filter = f"ass='{_escape_path_for_filtergraph(ass_path)}'"
    if fontsdir is not None:
        subtitle_filter += f":fontsdir='{_escape_path_for_filtergraph(Path(fontsdir))}'"
    video_codec = ["-c:v", "h264_nvenc"] if use_nvenc else ["-c:v", "libx264"]

    return [
        str(ffmpeg_path),
        "-y",
        "-i", str(video_path),
        "-vf", subtitle_filter,
        *video_codec,
        "-c:a", "copy",
        "-progress", "pipe:1",
        "-nostats",
        str(output_path),
    ]


def _escape_path_for_filtergraph(path: Path) -> str:
    """Escape a path for embedding inside an ffmpeg ``-vf`` filtergraph.

    Filtergraph syntax treats ``:``, ``\\`` and ``'`` specially. Windows
    paths routinely contain ``:`` (the drive letter) and ``\\``.
    """
    escaped = str(path).replace("\\", "/")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    return escaped


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


def burn_captions(
    video_path: Path | str,
    ass_path: Path | str,
    output_path: Path | str,
    *,
    duration_seconds: float,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    use_nvenc: bool | None = None,
    fontsdir: Path | str | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Burn ``ass_path``'s captions into ``video_path``, writing an MP4.

    Args:
        duration_seconds: total video duration, used to turn ffmpeg's raw
            ``out_time`` into a percentage. Callers get this from ffprobe
            or from an earlier step in the pipeline -- burn.py does not
            probe it itself.
        use_nvenc: force NVENC on (True) or off (False); ``None`` (default)
            auto-detects via ``detect_nvenc()``.
        fontsdir: directory libass should search for bundled fonts (spec
            7A.4) -- see ``build_burn_command``. ``None`` (the default)
            omits it entirely, unchanged from before this parameter
            existed.
        on_progress: called with a 0-100 float as ffmpeg reports progress.

    Raises:
        BurnInError: the video or ass file is missing, ffmpeg cannot be
            launched, or ffmpeg exits non-zero.
    """
    video_path = Path(video_path)
    ass_path = Path(ass_path)
    output_path = Path(output_path)

    if not video_path.is_file():
        raise BurnInError(f"Input video not found: {video_path}")
    if not ass_path.is_file():
        raise BurnInError(f"Subtitle file not found: {ass_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if use_nvenc is None:
        use_nvenc = detect_nvenc()

    args = build_burn_command(
        video_path,
        ass_path,
        output_path,
        ffmpeg_path=ffmpeg_path,
        use_nvenc=use_nvenc,
        fontsdir=fontsdir,
    )

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise BurnInError(f"Failed to launch ffmpeg at {ffmpeg_path}: {exc}") from exc

    if process.stdout is not None:
        for line in process.stdout:
            percentage = _parse_progress_line(line, duration_seconds)
            if percentage is not None and on_progress is not None:
                on_progress(percentage)

    stderr_output = process.stderr.read() if process.stderr is not None else ""
    returncode = process.wait()

    if returncode != 0:
        raise BurnInError(
            f"ffmpeg failed burning captions into {video_path.name} (exit {returncode})",
            stderr=stderr_output,
            returncode=returncode,
        )

    return output_path
