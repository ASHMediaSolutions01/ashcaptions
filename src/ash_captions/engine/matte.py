"""Person matting, so captions can sit *behind* the speaker (v0.4).

The effect the team asked for ("captions behind the character") is not a
caption style: it needs the software to know where the person is in every
frame. This module produces that knowledge as a **matte video** -- a
greyscale clip, white where the person is -- using Robust Video Matting
(RVM, MobileNetV3 variant) run through onnxruntime on the CPU. Measured on
the studio's real reel at 360x640: ~27 ms a frame, so a 60-second reel
mattes in under a minute and an hour-long file in roughly real time.

The burn then composites in one ffmpeg pass (``composite_filtergraph``):
captions are drawn on the frame, and the original frame masked by the
matte is laid back on top, so the person covers the caption wherever they
overlap. Everything stays in ffmpeg's filter graph; no frame ever passes
through Python except for the matte itself.

Model weights are not committed: ``ensure_matte_model`` downloads the
15 MB ONNX file into the models directory on first use (or the build
pre-seeds it), and refuses plainly when offline.
"""

from __future__ import annotations

import logging
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audio import DEFAULT_FFMPEG_PATH
from .ffmpeg_process import no_window_flags

log = logging.getLogger(__name__)

MATTE_MODEL_FILENAME = "rvm_mobilenetv3_fp32.onnx"
MATTE_MODEL_URL = (
    "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/" + MATTE_MODEL_FILENAME
)
MATTE_MODEL_MIN_BYTES = 10_000_000  # the real file is ~15 MB; anything smaller is an HTML error page

# Working resolution for inference: the short side of the frame. 480 keeps
# hair and hands clean at 1080p and stays above 20 fps on a laptop CPU;
# the matte is upscaled to the frame size when composited.
DEFAULT_SHORT_SIDE = 480

ProgressCallback = Callable[[float], None]
StopCheck = Callable[[], bool]


class MatteError(Exception):
    """The matte could not be produced: no model, no onnxruntime, ffmpeg failed."""


class MatteCancelled(MatteError):
    pass


# ---------------------------------------------------------------------------
# model file
# ---------------------------------------------------------------------------


def matte_model_path(models_dir: Path | str) -> Path:
    return Path(models_dir) / MATTE_MODEL_FILENAME


def ensure_matte_model(models_dir: Path | str, *, download: bool = True, timeout: float = 120) -> Path:
    """The model file, downloading it once if allowed. Raises MatteError
    with a plain message when it is absent and cannot be fetched."""
    path = matte_model_path(models_dir)
    if path.is_file() and path.stat().st_size >= MATTE_MODEL_MIN_BYTES:
        return path
    if not download:
        raise MatteError(
            f"The person-matting model is not installed ({path}). Run "
            "scripts/fetch_matte_model.py, or connect to the internet for the first use."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    log.info("downloading the matting model to %s", path)
    try:
        with urllib.request.urlopen(MATTE_MODEL_URL, timeout=timeout) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1024 * 1024):
                out.write(chunk)
        if tmp.stat().st_size < MATTE_MODEL_MIN_BYTES:
            raise MatteError("The downloaded matting model is too small to be real; try again later.")
        tmp.replace(path)
    except MatteError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - URLError, OSError, timeouts: one plain message
        tmp.unlink(missing_ok=True)
        raise MatteError(
            "Could not download the person-matting model (no internet?). Captions behind "
            f"the speaker need it once: {exc}"
        ) from exc
    return path


# ---------------------------------------------------------------------------
# inference
# ---------------------------------------------------------------------------


def working_size(width: int, height: int, *, short_side: int = DEFAULT_SHORT_SIDE) -> tuple[int, int]:
    """Inference frame size: the short side capped at ``short_side``, aspect
    kept, both dimensions even (ffmpeg's yuv420p needs even sizes)."""
    if width <= 0 or height <= 0:
        return (short_side, short_side)
    scale = min(1.0, short_side / min(width, height))
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return (w, h)


def _downsample_ratio(w: int, h: int) -> float:
    # RVM's own guidance: ~0.25 for 1080p, higher for smaller inputs.
    return 0.4 if max(w, h) <= 720 else 0.25


class _RvmSession:
    """One RVM session with its recurrent state. Frames must be fed in
    order; the state carries the previous frame's memory, which is what
    keeps the matte stable from frame to frame."""

    def __init__(self, model_path: Path, *, threads: int | None = None) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - both ship with faster-whisper
            raise MatteError(f"onnxruntime/numpy are not available: {exc}") from exc
        self._np = np
        options = ort.SessionOptions()
        if threads:
            options.intra_op_num_threads = int(threads)
        self._session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self._rec = [np.zeros([1, 1, 1, 1], dtype=np.float32)] * 4

    def alpha(self, rgb_frame: bytes, w: int, h: int) -> bytes:
        """Grey (0-255) alpha for one RGB24 frame of ``w`` x ``h``."""
        np = self._np
        src = np.frombuffer(rgb_frame, np.uint8).reshape(h, w, 3).astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        _fgr, pha, *self._rec = self._session.run(
            None,
            {
                "src": src,
                "r1i": self._rec[0], "r2i": self._rec[1], "r3i": self._rec[2], "r4i": self._rec[3],
                "downsample_ratio": np.array([_downsample_ratio(w, h)], dtype=np.float32),
            },
        )
        return (np.clip(pha[0, 0], 0.0, 1.0) * 255.0).astype(np.uint8).tobytes()


@dataclass(frozen=True)
class MatteResult:
    path: Path
    width: int
    height: int
    fps: float
    frames: int


def render_matte(
    video_path: Path | str,
    matte_path: Path | str,
    *,
    model_path: Path | str,
    width: int,
    height: int,
    fps: float,
    duration_seconds: float,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    short_side: int = DEFAULT_SHORT_SIDE,
    threads: int | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    session_factory: Callable[[Path], object] | None = None,
) -> MatteResult:
    """Write a greyscale matte video of ``video_path`` to ``matte_path``.

    Frames are decoded by ffmpeg at the working size and constant ``fps``,
    fed through RVM one by one, and the alpha planes are encoded by a
    second ffmpeg into an H.264 clip at the same size and rate. The
    constant rate matters: the burn converts the source to the same rate,
    so the two line up frame for frame in ``alphamerge``.

    ``session_factory`` exists for tests: it receives the model path and
    returns an object with ``.alpha(rgb_bytes, w, h) -> bytes``.
    """
    video_path = Path(video_path)
    matte_path = Path(matte_path)
    if fps <= 0:
        fps = 30.0
    w, h = working_size(width, height, short_side=short_side)
    frame_bytes = w * h * 3
    total_frames = max(1, int(round(duration_seconds * fps)))
    session = (session_factory or (lambda p: _RvmSession(Path(p), threads=threads)))(Path(model_path))

    decode = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(video_path),
        "-map", "0:v:0", "-vf", f"fps={fps:g},scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    encode = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
        "-an", str(matte_path),
    ]
    flags = no_window_flags()
    matte_path.parent.mkdir(parents=True, exist_ok=True)
    frames = 0
    try:
        with subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags) as dec, \
                subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=subprocess.PIPE, **flags) as enc:
            assert dec.stdout is not None and enc.stdin is not None
            try:
                while True:
                    if should_stop is not None and should_stop():
                        raise MatteCancelled(f"Matting of {video_path.name} was cancelled")
                    frame = dec.stdout.read(frame_bytes)
                    if len(frame) < frame_bytes:
                        break
                    enc.stdin.write(session.alpha(frame, w, h))
                    frames += 1
                    if on_progress is not None and frames % 10 == 0:
                        on_progress(min(100.0, 100.0 * frames / total_frames))
            finally:
                try:
                    enc.stdin.close()
                except OSError:
                    pass
                dec.stdout.close()
            dec_err = dec.stderr.read().decode("utf-8", "replace") if dec.stderr else ""
            enc_err = enc.stderr.read().decode("utf-8", "replace") if enc.stderr else ""
            dec.wait()
            enc.wait()
    except MatteCancelled:
        matte_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        matte_path.unlink(missing_ok=True)
        raise MatteError(f"Could not run ffmpeg for matting: {exc}") from exc
    if frames == 0 or dec.returncode != 0 or enc.returncode != 0:
        matte_path.unlink(missing_ok=True)
        raise MatteError(
            f"Matting {video_path.name} produced no usable matte "
            f"(decode exit {dec.returncode}, encode exit {enc.returncode}). {dec_err[-300:]} {enc_err[-300:]}".strip()
        )
    if on_progress is not None:
        on_progress(100.0)
    return MatteResult(path=matte_path, width=w, height=h, fps=fps, frames=frames)


# ---------------------------------------------------------------------------
# compositing
# ---------------------------------------------------------------------------


def composite_filtergraph(
    *,
    caption_filter: str,
    width: int,
    height: int,
    fps: float,
    punch_filter: str | None = None,
) -> str:
    """The ``-filter_complex`` graph for captions behind the speaker.

    Input 0 is the source video, input 1 the matte. The source is made
    constant-rate (matching the matte), optionally punched, then split:
    one copy gets the captions; the other is masked by the upscaled matte
    and laid over the captioned copy, so the person covers the captions.
    The punch is applied to the matte too, so the two stay aligned.
    """
    if fps <= 0:
        fps = 30.0
    punch = f",{punch_filter}" if punch_filter else ""
    return (
        f"[0:v]fps={fps:g}{punch}[base];"
        "[base]split[b1][b2];"
        f"[b1]{caption_filter}[cap];"
        f"[1:v]fps={fps:g},scale={width}:{height}:flags=bicubic,format=gray{punch}[al];"
        "[b2][al]alphamerge[fg];"
        "[cap][fg]overlay=0:0:format=auto,format=yuv420p[out]"
    )
