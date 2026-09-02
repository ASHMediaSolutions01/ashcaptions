"""Read a video's dimensions, frame rate and duration via ffprobe.

Burn-in has always taken ``duration_seconds`` from its caller rather than
probing, which was fine when the only use was a progress percentage. The
punch-in filter needs the real frame size and rate as well -- ``zoompan``
must be told its output size explicitly, and getting it wrong silently
rescales the whole video -- so this reads them from the file itself.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FFPROBE_PATH = Path("bin") / "ffprobe.exe"


class ProbeError(Exception):
    """Raised when a video's properties cannot be read."""


@dataclass(frozen=True, slots=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration_seconds: float

    @property
    def size_arg(self) -> str:
        """The ``WxH`` string ffmpeg's ``s=`` options expect."""
        return f"{self.width}x{self.height}"


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


def probe_video(
    video_path: Path | str, *, ffprobe_path: Path | str = DEFAULT_FFPROBE_PATH
) -> VideoInfo:
    """Read the first video stream's width, height, fps and duration."""
    video_path = Path(video_path)
    if not video_path.is_file():
        raise ProbeError(f"Input video not found: {video_path}")

    args = [
        str(ffprobe_path),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f"Could not run ffprobe on {video_path.name}: {exc}") from exc

    if result.returncode != 0:
        raise ProbeError(
            f"ffprobe failed reading {video_path.name} (exit {result.returncode})"
        )

    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProbeError(
            f"ffprobe gave no usable video stream for {video_path.name}"
        ) from exc

    fps = _parse_fraction(str(stream.get("r_frame_rate", "0/0")))
    try:
        duration = float(payload.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    return VideoInfo(width=width, height=height, fps=fps, duration_seconds=duration)
