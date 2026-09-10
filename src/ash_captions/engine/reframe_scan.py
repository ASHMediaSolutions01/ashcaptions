"""Look at the footage and produce a crop plan for ``engine.reframe``.

``reframe.py`` is pure: it decides where the crop sits given measurements.
This is the half that goes and gets the measurements -- one ffmpeg pass
for the cuts, then a handful of decoded frames per shot through RVM.

Sampling rather than matting every frame is not an optimisation, it is
the design, and it follows from a measurement: inside a shot the subject
drifts 13-31 px in a 1920-wide frame over twelve seconds, so five frames
describe a shot as well as three hundred do. Measured on the studio's
4:48 interview, that is 120 inferences (~3.6 s) instead of 8658 (~260 s)
-- the difference between a reel an editor waits for and one they give up
on.

Nothing here decides anything. Every judgement lives in ``reframe.py``,
where it can be tested without ffmpeg or a model file.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable

from .audio import DEFAULT_FFMPEG_PATH
from .ffmpeg_process import no_window_flags
from .matte import MatteError, ensure_matte_model, open_session, working_size
from .reframe import CropPlan, Sample, blobs_from_columns, plan_crops, shots_from_cuts

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]
StopCheck = Callable[[], bool]

# Frames matted per shot. Five spreads across the shot's middle 70% and
# still catches a dissolve that cut detection missed -- the measured case
# needed only that the samples disagree with each other.
SAMPLES_PER_SHOT = 5

# ffmpeg's scene score for a hard cut. 0.30 found 24 real cuts in the
# measured clip and no false ones; lowering it to 0.10 added six more
# that were camera movement, and still did not find the dissolve, which
# is why `reframe.split_on_subject_change` exists instead.
SCENE_THRESHOLD = 0.30

# Sampling inside the shot rather than at its edges: a cut's own frames
# carry motion blur and half-faded pictures.
SHOT_INSET = 0.15


class ReframeScanError(Exception):
    """The footage could not be scanned for a crop plan."""


def detect_cuts(
    video_path: Path | str,
    *,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    threshold: float = SCENE_THRESHOLD,
) -> tuple[float, ...]:
    """Timestamps where ffmpeg's own scene score says the picture changed.

    Hard cuts only. A cross-dissolve never spikes the score at any
    threshold -- measured down to 0.10 on a title card fading into a
    two-shot -- so this deliberately under-reports and the sampling
    catches the rest.
    """
    command = [
        str(ffmpeg_path), "-hide_banner", "-nostdin",
        "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold:.2f})',showinfo",
        "-an", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, **no_window_flags()
        )
    except OSError as exc:
        raise ReframeScanError(f"Could not run ffmpeg to find the cuts: {exc}") from exc

    times: list[float] = []
    for line in proc.stderr.splitlines():
        if "pts_time:" not in line:
            continue
        try:
            times.append(float(line.split("pts_time:")[1].split()[0]))
        except (IndexError, ValueError):
            continue
    return tuple(sorted(times))


def _decode_frame(
    video_path: Path,
    when: float,
    width: int,
    height: int,
    ffmpeg_path: Path | str,
) -> bytes | None:
    """One RGB24 frame at ``when``, scaled to the inference size."""
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{when:.3f}", "-i", str(video_path),
        "-map", "0:v:0", "-frames:v", "1",
        "-vf", f"scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, **no_window_flags())
    except OSError as exc:
        raise ReframeScanError(f"Could not run ffmpeg to read a frame: {exc}") from exc
    expected = width * height * 3
    return proc.stdout if len(proc.stdout) == expected else None


def _sample_times(start: float, end: float, count: int) -> list[float]:
    first = start + (end - start) * SHOT_INSET
    last = end - (end - start) * SHOT_INSET
    if count <= 1 or last <= first:
        return [(first + last) / 2.0]
    step = (last - first) / (count - 1)
    return [first + step * i for i in range(count)]


def scan_reframe(
    video_path: Path | str,
    *,
    width: int,
    height: int,
    duration_seconds: float,
    models_dir: Path | str,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    samples_per_shot: int = SAMPLES_PER_SHOT,
    threads: int | None = None,
    overrides: dict[int, int] | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> CropPlan:
    """Watch ``video_path`` and return where the 9:16 crop should sit.

    ``overrides`` maps a window index to the person an editor picked; it
    is threaded through to ``reframe.plan_crops`` so a corrected plan can
    be rebuilt from the same footage.
    """
    video_path = Path(video_path)
    if width <= 0 or height <= 0:
        raise ReframeScanError(
            "Reframing needs the video's frame size, and ffprobe could not read it."
        )
    if duration_seconds <= 0:
        raise ReframeScanError("Reframing needs the video's duration, which reads as zero.")

    try:
        model_path = ensure_matte_model(models_dir, download=True)
    except MatteError as exc:
        raise ReframeScanError(str(exc)) from exc

    cuts = detect_cuts(video_path, ffmpeg_path=ffmpeg_path)
    shots = shots_from_cuts(cuts, duration_seconds)
    log.info("reframe: %d cut(s) -> %d shot(s) in %.0fs", len(cuts), len(shots), duration_seconds)

    infer_w, infer_h = working_size(width, height)
    session = open_session(model_path, threads=threads)
    samples_by_shot: list[list[Sample]] = []
    total = max(1, len(shots) * samples_per_shot)
    done = 0

    for shot in shots:
        samples: list[Sample] = []
        for when in _sample_times(shot.start, shot.end, samples_per_shot):
            if should_stop is not None and should_stop():
                raise ReframeScanError(f"Reframing {video_path.name} was cancelled")
            frame = _decode_frame(video_path, when, infer_w, infer_h, ffmpeg_path)
            done += 1
            if on_progress is not None:
                on_progress(min(100.0, 100.0 * done / total))
            if frame is None:
                continue
            columns = _column_sums(session.alpha(frame, infer_w, infer_h), infer_w, infer_h)
            samples.append(
                Sample(time=when, blobs=blobs_from_columns(columns, source_width=width))
            )
        samples_by_shot.append(samples)

    plan = plan_crops(
        shots,
        samples_by_shot,
        source_width=width,
        source_height=height,
        overrides=overrides,
    )
    log.info(
        "reframe: %d window(s), %d of them with more than one person to choose between",
        len(plan.windows),
        len(plan.contested_windows),
    )
    if on_progress is not None:
        on_progress(100.0)
    return plan


def _column_sums(alpha: bytes, width: int, height: int) -> list[float]:
    """Per-column totals of one greyscale alpha plane.

    numpy ships with the bundle (faster-whisper depends on it) and does
    this in one pass; the pure-Python fallback exists so a stripped
    environment degrades to slow rather than to broken.
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with faster-whisper
        totals = [0.0] * width
        for row in range(height):
            base = row * width
            for col in range(width):
                totals[col] += alpha[base + col]
        return totals
    plane = np.frombuffer(alpha, dtype=np.uint8).reshape(height, width)
    return plane.sum(axis=0, dtype=np.float64).tolist()
