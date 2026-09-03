"""Which H.264 encoder to use, and the ffmpeg-version-dependent options.

Split out of ``burn.py`` (v0.4) purely for size; the behaviour is the
same. Everything here probes the *shipped* ffmpeg binary at run time
rather than assuming what it contains -- an LGPL build has no libx264,
a machine with an NVIDIA driver may still fail to encode with NVENC, and
ffmpeg 8 dropped ``-filter_script``. Results are cached per binary path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .audio import DEFAULT_FFMPEG_PATH
from .burn_errors import BurnInError
from .ffmpeg_process import no_window_flags

DEFAULT_NVIDIA_SMI_PATH = "nvidia-smi"

# Software H.264 encoders in preference order. libx264 is the quality and
# speed benchmark and is what we ship (BtbN's GPL build). It is GPL, so an
# LGPL ffmpeg build cannot contain it -- BtbN's LGPL build is configured
# --disable-libx264 and burning with it fails outright with "Unknown encoder
# 'libx264'", which is how the tool was first broken. h264_mf is Windows
# MediaFoundation, present on any Windows machine and able to produce a
# High-profile stream; libopenh264 (Cisco, BSD) is constrained baseline
# only, so it is the last resort rather than the second choice.
#
# Which of these is present is a property of the ffmpeg binary we ship, not
# of this code, so the encoder is probed at run time rather than assumed.
SOFTWARE_H264_ENCODERS = ("libx264", "h264_mf", "libopenh264")

_encoder_cache: dict[str, frozenset[str]] = {}

_version_cache: dict[str, int | None] = {}

# ---------------------------------------------------------------------------
# probing the binary
# ---------------------------------------------------------------------------


def _run_quiet(args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_window_flags(),
    )


def available_encoders(ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH) -> frozenset[str]:
    """Encoder names the given ffmpeg binary actually supports.

    Cached per binary path: shelling out to ``-encoders`` costs ~100ms and
    the answer cannot change for a given file.
    """
    key = str(ffmpeg_path)
    cached = _encoder_cache.get(key)
    if cached is not None:
        return cached
    try:
        result = _run_quiet([str(ffmpeg_path), "-hide_banner", "-encoders"], timeout=30)
    except Exception:  # noqa: BLE001
        # Probing is a convenience, never a precondition for burning: a
        # missing binary, a permissions error or a stubbed subprocess must
        # all mean "cannot tell", not "cannot burn". Callers fall back to
        # the historical default when this returns nothing.
        return frozenset()
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # Encoder lines look like " V....D libx264   H.264 ..." -- the flag
        # column is exactly six characters, which is what distinguishes them
        # from the header and the legend above it.
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    found = frozenset(names)
    _encoder_cache[key] = found
    return found


_VERSION_RE = re.compile(r"ffmpeg version (?:n)?(\d+)\.")


def ffmpeg_major_version(ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH) -> int | None:
    """``6`` for "ffmpeg version 6.1.1", ``None`` for a git build ("N-126386-g...")
    or a binary that cannot be run."""
    key = str(ffmpeg_path)
    if key in _version_cache:
        return _version_cache[key]
    major: int | None = None
    try:
        result = _run_quiet([str(ffmpeg_path), "-hide_banner", "-version"], timeout=30)
        match = _VERSION_RE.search(result.stdout)
        if match:
            major = int(match.group(1))
    except Exception:  # noqa: BLE001
        return None
    _version_cache[key] = major
    return major


def filter_file_option(ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH, *, complex_graph: bool = False) -> str:
    """The option that reads the (video or complex) filtergraph from a file.

    ffmpeg 7 introduced ``-/option file`` (read any option's value from a
    file) and deprecated ``-filter_script``; ffmpeg 8 removed it. Git
    builds carry no numeric version and are always recent.
    """
    major = ffmpeg_major_version(ffmpeg_path)
    if major is not None and major < 7:
        return "-filter_complex_script" if complex_graph else "-filter_script:v"
    return "-/filter_complex" if complex_graph else "-/filter:v"


def detect_nvenc(*, nvidia_smi_path: str = DEFAULT_NVIDIA_SMI_PATH) -> bool:
    """Return True if an NVIDIA GPU (and thus NVENC) looks available.

    Per spec 11.2, ``nvidia-smi`` presence is a reliable signal -- it ships
    with the display driver, not only the CUDA toolkit. Whether *this*
    ffmpeg can drive it is a separate question (``nvenc_encode_works``).
    """
    try:
        result = subprocess.run(
            [nvidia_smi_path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            **no_window_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def nvenc_encode_works(ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH) -> bool:
    """Encode one black frame with ``h264_nvenc``; cached per binary.

    ``nvidia-smi`` being present says there is a driver, not that the
    driver is new enough for this ffmpeg's NVENC SDK -- the mismatch fails
    at the *start* of a burn, after the editor has walked away. A 20s
    test up front turns that into a silent software fallback.
    """
    key = str(ffmpeg_path)
    cached = _nvenc_test_cache.get(key)
    if cached is not None:
        return cached
    args = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "nullsrc=s=256x256:r=30",
        "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
    ]
    try:
        works = _run_quiet(args, timeout=20).returncode == 0
    except Exception:  # noqa: BLE001
        works = False
    _nvenc_test_cache[key] = works
    return works


def select_video_encoder(
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH, *, use_nvenc: bool = False
) -> str:
    """Pick an H.264 encoder this ffmpeg build can actually run.

    Raises ``BurnInError`` naming the problem rather than letting ffmpeg
    fail later with its own less actionable "Encoder not found".
    """
    encoders = available_encoders(ffmpeg_path)
    if use_nvenc:
        if not encoders:
            # Probing failed (a mocked or missing binary): trust the request.
            return "h264_nvenc"
        if "h264_nvenc" in encoders and nvenc_encode_works(ffmpeg_path):
            return "h264_nvenc"
        # NVENC was asked for but cannot run here: software, quietly.
    if not encoders:
        # Probing failed. Assume the historical default rather than
        # refusing to build a command at all.
        return "libx264"
    for name in SOFTWARE_H264_ENCODERS:
        if name in encoders:
            return name
    raise BurnInError(
        f"{ffmpeg_path} has no usable H.264 encoder "
        f"(looked for {', '.join(SOFTWARE_H264_ENCODERS)}). "
        "An LGPL ffmpeg build excludes libx264; run scripts/fetch_ffmpeg.py "
        "to get the GPL build we ship, or use a build with one of these."
    )


# ---------------------------------------------------------------------------
# command construction


# Bitrate for encoders without a quality mode (libopenh264, h264_mf),
# interpolated linearly in pixel count between these two anchors.
BITRATE_1080P = 12_000_000
BITRATE_4K = 45_000_000
MIN_BITRATE = 2_000_000
_PIXELS_1080P = 1920 * 1080
_PIXELS_4K = 3840 * 2160

# Audio codecs MP4 can carry as-is. Anything else (PCM from a camera MOV,
# Opus from a screen recorder) must be re-encoded or the mux fails with
# "could not find tag for codec".
COPYABLE_AUDIO_CODECS = frozenset({"aac", "mp3"})

_nvenc_test_cache: dict[str, bool] = {}


# ---------------------------------------------------------------------------


def software_bitrate(width: int, height: int) -> int:
    """Target bitrate for encoders without a quality mode, linear in pixel
    count: 12 Mbit/s at 1080p, 45 Mbit/s at 4K. Unknown size means 1080p."""
    pixels = width * height if width > 0 and height > 0 else _PIXELS_1080P
    slope = (BITRATE_4K - BITRATE_1080P) / (_PIXELS_4K - _PIXELS_1080P)
    return int(max(MIN_BITRATE, BITRATE_1080P + slope * (pixels - _PIXELS_1080P)))


def encoder_args(encoder: str, *, width: int = 0, height: int = 0) -> list[str]:
    """``-c:v`` plus explicit rate control for ``encoder``.

    Every branch ends in ``-pix_fmt yuv420p``: ffmpeg appends the format
    conversion *after* the filtergraph, so a 10-bit source is captioned
    at 10 bits and only then reduced to the 8-bit 4:2:0 every player
    expects.
    """
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    return ["-c:v", encoder, "-b:v", str(software_bitrate(width, height)), "-pix_fmt", "yuv420p"]


def audio_args(audio_codec: str | None) -> list[str]:
    """Copy the audio when MP4 can carry it, otherwise re-encode to AAC.
    ``None`` (codec unknown, or no audio) keeps the historical copy."""
    if audio_codec is None or audio_codec.lower() in COPYABLE_AUDIO_CODECS:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]
