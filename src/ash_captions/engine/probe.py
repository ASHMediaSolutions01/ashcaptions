"""Read a video's dimensions, frame rate, duration and audio codec via ffprobe.

Burn-in has always taken ``duration_seconds`` from its caller rather than
probing, which was fine when the only use was a progress percentage. The
burn now also needs the frame size (to scale the software encoders'
bitrate) and the audio codec (to know whether ``-c:a copy`` can go into
an MP4 at all), so this reads them from the file itself.

Frame rate comes from ``avg_frame_rate``, not ``r_frame_rate``. The
latter is the *smallest interval seen* expressed as a rate, and on a
variable-frame-rate phone recording it reports nonsense like ``1000/1``
or ``90000/1``. Nothing in the burn depends on the rate any more (the
punch filter follows frame timestamps), but a caller that does should
at least get the average, sanity-checked.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_process import no_window_flags

DEFAULT_FFPROBE_PATH = Path("bin") / "ffprobe.exe"

# Nothing the studio shoots is above 120 fps; a probed rate past this is a
# VFR artefact, not a frame rate. 30 is the safe guess for phone footage.
MAX_PLAUSIBLE_FPS = 120.0
FALLBACK_FPS = 30.0

# A 90-minute 4K master on a slow external drive can take ffprobe a while
# to index; 60s was cutting it fine.
PROBE_TIMEOUT_SECONDS = 180


class ProbeError(Exception):
    """Raised when a video's properties cannot be read."""


@dataclass(frozen=True, slots=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration_seconds: float
    audio_codec: str | None = None  # ffprobe's codec_name of the first audio stream

    @property
    def size_arg(self) -> str:
        """The ``WxH`` string ffmpeg's ``s=`` options expect."""
        return f"{self.width}x{self.height}"


def ffprobe_beside(ffmpeg_path: Path | str) -> Path:
    """The ffprobe that ships next to ``ffmpeg_path`` (same build, same
    directory); a bare ``ffmpeg`` on PATH maps to a bare ``ffprobe``."""
    ffmpeg_path = Path(ffmpeg_path)
    return ffmpeg_path.with_name(ffmpeg_path.name.replace("ffmpeg", "ffprobe", 1))


def _parse_fraction(value: str) -> float:
    """ffprobe reports frame rates as ``30/1``; a bad or zero denominator
    must not raise, since a still or odd stream can legitimately report
    ``0/0``."""
    try:
        if "/" in value:
            num, _, den = value.partition("/")
            denominator = float(den)
            if denominator == 0:
                return 0.0
            return float(num) / denominator
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def sane_fps(value: float) -> float:
    """Clamp a probed frame rate to something a real camera produces."""
    if not math.isfinite(value) or value <= 0 or value > MAX_PLAUSIBLE_FPS:
        return FALLBACK_FPS
    return value


def probe_video(
    video_path: Path | str, *, ffprobe_path: Path | str = DEFAULT_FFPROBE_PATH
) -> VideoInfo:
    """Read the first video stream's size, average fps and duration, and
    the first audio stream's codec name (``None`` when there is no audio)."""
    video_path = Path(video_path)
    if not video_path.is_file():
        raise ProbeError(f"Input video not found: {video_path}")

    args = [
        str(ffprobe_path),
        "-v", "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration",
        "-of", "json",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            **no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f"Could not run ffprobe on {video_path.name}: {exc}") from exc

    if result.returncode != 0:
        raise ProbeError(
            f"ffprobe failed reading {video_path.name} (exit {result.returncode})"
        )

    try:
        payload = json.loads(result.stdout)
        streams = [s for s in payload.get("streams", []) if isinstance(s, dict)]
        video = next(s for s in streams if s.get("codec_type") == "video")
        width = int(video["width"])
        height = int(video["height"])
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError, AttributeError) as exc:
        raise ProbeError(
            f"ffprobe gave no usable video stream for {video_path.name}"
        ) from exc

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    audio_codec = None
    if audio is not None:
        name = audio.get("codec_name")
        audio_codec = str(name) if name else None

    fps = sane_fps(_parse_fraction(str(video.get("avg_frame_rate", "0/0"))))
    try:
        duration = float(payload.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    return VideoInfo(
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration,
        audio_codec=audio_codec,
    )
