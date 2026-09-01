"""Builds the ffmpeg command for a style preview (spec 7A.3).

The style editor's whole pitch is "not a config file": pick a style,
render a few seconds of the editor's *actual* video with it burned in,
and show that inline. This module only builds that ffmpeg command --
pure argument construction, no subprocess, no filesystem access -- so it
is testable without ffmpeg installed. The caller (the web layer) has
already rendered the chosen style to an ``.ass`` file via
``ash_captions.styles.render.write_ass`` and supplies its path here.

Cutting a short clip and burning captions in one ffmpeg invocation is
what makes this fast enough to feel live: ``-ss`` placed *before* ``-i``
seeks in the demuxer rather than decoding from the start of the file, so
a 3-second preview costs roughly 3 seconds of work regardless of how
long the source video is.
"""
from __future__ import annotations

from pathlib import Path

from ..engine.audio import DEFAULT_FFMPEG_PATH
from .fonts import fontsdir_arg

DEFAULT_PREVIEW_DURATION_SECONDS = 3.0


def build_preview_command(
    video_path: Path | str,
    ass_path: Path | str,
    output_path: Path | str,
    *,
    start_seconds: float,
    duration_seconds: float = DEFAULT_PREVIEW_DURATION_SECONDS,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    fonts_dir: Path | str | None = None,
) -> list[str]:
    """Construct the ffmpeg argv for a short, styled preview clip.

    Args:
        video_path: the editor's source video (spec 7A.3: "the editor's
            actual video", not a generic sample).
        ass_path: an already-rendered ``.ass`` file for the style being
            previewed.
        output_path: where the preview clip is written.
        start_seconds: where in ``video_path`` the preview starts.
        duration_seconds: preview length; defaults to ~3 seconds.
        fonts_dir: directory libass should search for bundled fonts
            (``fonts.assets_fonts_dir()`` by default) so the preview
            renders correctly without any font being installed into
            Windows (spec 7A.4).
    """
    if start_seconds < 0:
        raise ValueError(f"start_seconds must be >= 0, got {start_seconds}")
    if duration_seconds <= 0:
        raise ValueError(f"duration_seconds must be > 0, got {duration_seconds}")

    video_path = Path(video_path)
    ass_path = Path(ass_path)
    output_path = Path(output_path)
    fonts_directory = Path(fonts_dir) if fonts_dir is not None else _default_fonts_dir()

    subtitle_filter = (
        f"subtitles='{_escape_path_for_filtergraph(ass_path)}'"
        f":fontsdir='{_escape_path_for_filtergraph(fonts_directory)}'"
    )

    # Same reason burn.py probes rather than hardcodes: an LGPL ffmpeg has
    # no libx264 (x264 is GPL), so a fixed encoder name fails outright with
    # "Unknown encoder" -- which is exactly how the live preview broke.
    from ash_captions.engine.burn import select_video_encoder

    encoder = select_video_encoder(ffmpeg_path, use_nvenc=False)

    command = [
        str(ffmpeg_path),
        "-y",
        "-ss", _format_seconds(start_seconds),
        "-t", _format_seconds(duration_seconds),
        "-i", str(video_path),
        "-vf", subtitle_filter,
        "-c:v", encoder,
    ]
    # -preset is an x264/x265 option; libopenh264 and the hardware encoders
    # reject it, so only pass it to an encoder that understands it.
    if encoder in ("libx264", "libx265"):
        command += ["-preset", "veryfast"]
    command += ["-an", str(output_path)]
    return command


def _default_fonts_dir() -> Path:
    return Path(fontsdir_arg())


def _format_seconds(seconds: float) -> str:
    return f"{seconds:.3f}".rstrip("0").rstrip(".") or "0"


def _escape_path_for_filtergraph(path: Path) -> str:
    """Escape a path for embedding inside an ffmpeg ``-vf`` filtergraph.

    Filtergraph syntax treats ``:``, ``\\`` and ``'`` specially. Windows
    paths routinely contain ``:`` (the drive letter) and ``\\``. Mirrors
    ``engine.burn._escape_path_for_filtergraph`` -- duplicated rather than
    imported since that's a private helper in a module this package
    doesn't own.
    """
    escaped = str(path).replace("\\", "/")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    return escaped
