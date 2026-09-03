"""Burn captions into video via ffmpeg, using the .ass file as a subtitle filter.

Optional step (spec 8, 10) producing ``*.captioned.mp4``. Uses NVENC when
the machine has an NVIDIA GPU that this ffmpeg can actually drive
(``nvidia-smi`` presence per spec 11.2, confirmed by a one-frame test
encode), falling back to a software H.264 encoder otherwise. Progress
comes from ffmpeg's ``-progress`` output turned into a percentage; that
needs the video's total duration, which the caller supplies.

How a burn is laid out on disk, and why:

* **Nothing user-derived goes into the filtergraph.** A filename is
  filter syntax once it is inside ``ass='...'``: an apostrophe closes the
  quote and whatever follows is parsed as filter options. So the ``.ass``
  is copied to a fixed name (``captions.ass``) in a private work
  directory, ffmpeg runs with that directory as ``cwd``, and the graph
  says ``ass=captions.ass``. The only path left in the graph is the
  app's own fonts directory, which is escaped anyway.
* **The filtergraph is read from a file** (``filter.txt`` in the same
  directory) rather than passed as an argument: a 60-minute talk's
  punch-in envelope runs to ~80 characters per moment, and Windows caps
  a command line at 32,767 characters.
* **The output is written to a ``.part`` file** next to the final name
  and renamed onto it only when ffmpeg exits 0. A crash, a kill or a
  cancel never leaves a half-encoded file under the deliverable's name.

Bundled fonts (spec 7A.4) only resolve if libass is told where to find
them: ``fontsdir`` is supplied by the caller (typically
``ash_captions.styles.fontsdir_arg()``) -- this module doesn't assume it,
to keep the engine package decoupled from the styles package.

Killing a burn from outside (tray Quit): ``ffmpeg_process.active_processes()``
lists the running ffmpeg processes and ``kill_active_processes()`` kills
them; the burn that owned each one then fails with ``BurnInError`` and
removes its part file. Cancelling from inside: pass ``should_stop``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .audio import DEFAULT_FFMPEG_PATH
from .burn_errors import BurnCancelled, BurnInError
from .encoders import (  # noqa: F401 - re-exported; the pipeline and tests import these from here
    BITRATE_1080P,
    BITRATE_4K,
    DEFAULT_NVIDIA_SMI_PATH,
    MIN_BITRATE,
    SOFTWARE_H264_ENCODERS,
    audio_args,
    available_encoders,
    detect_nvenc,
    encoder_args,
    ffmpeg_major_version,
    filter_file_option,
    nvenc_encode_works,
    select_video_encoder,
    software_bitrate,
)
from .ffmpeg_process import (
    ProgressCallback,
    StopCheck,
    _parse_progress_line,  # noqa: F401  (re-exported for tests and callers)
    no_window_flags,
    run_ffmpeg,
)
from .probe import ProbeError, VideoInfo, ffprobe_beside, probe_video


# Fixed names inside the per-burn work directory. Fixed is the point: the
# filtergraph references them, so they must contain nothing a user chose.
CAPTIONS_FILENAME = "captions.ass"
FILTER_SCRIPT_FILENAME = "filter.txt"


def _escape_path_for_filtergraph(path: Path) -> str:
    """Escape a path for use inside a single-quoted filter option value.

    A filter option value is parsed twice: the graph parser strips the
    quotes (keeping backslashes), then the option parser applies
    backslash escapes and splits on ``:``. So ``\\`` becomes ``/``
    (Windows separators), ``:`` becomes ``\\:`` (survives level one, is
    unescaped at level two), and ``'`` -- which ends the quoted section
    -- is written as ``'\\\\\\''``: close the quote, an escaped
    backslash and an escaped quote (which level one turns into ``\\'``
    and level two into ``'``), reopen the quote.
    """
    escaped = str(path).replace("\\", "/")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"'\\\''")
    return escaped


def build_filtergraph(*, fontsdir: Path | str | None = None, punch_filter: str | None = None) -> str:
    """The ``-vf`` chain: punch-in (if any), then ``ass=captions.ass``.

    Punch-in goes first so captions are drawn on top at their true size.
    Chained the other way round the zoom magnifies the captions too, which
    looks like a rendering fault rather than an edit.
    """
    subtitle_filter = f"ass={CAPTIONS_FILENAME}"
    if fontsdir is not None:
        subtitle_filter += f":fontsdir='{_escape_path_for_filtergraph(Path(fontsdir))}'"
    if punch_filter:
        return f"{punch_filter},{subtitle_filter}"
    return subtitle_filter


def part_path_for(output_path: Path | str) -> Path:
    """``x.captioned.mp4`` -> ``x.captioned.part.mp4``, in the same directory
    so the final ``os.replace`` is an atomic rename, not a copy."""
    output_path = Path(output_path)
    suffix = output_path.suffix or ".mp4"
    return output_path.with_name(f"{output_path.stem}.part{suffix}")


def _executable(ffmpeg_path: Path | str) -> str:
    """Absolute path for a path-like binary (``cwd`` changes under it); a
    bare name is left for PATH lookup."""
    text = str(ffmpeg_path)
    if os.sep in text or "/" in text:
        return os.path.abspath(text)
    return text


def build_burn_command(
    video_path: Path | str,
    ass_path: Path | str,
    output_path: Path | str,
    *,
    work_dir: Path | str,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    use_nvenc: bool = False,
    fontsdir: Path | str | None = None,
    punch_filter: str | None = None,
    audio_codec: str | None = None,
    width: int = 0,
    height: int = 0,
    matte_path: Path | str | None = None,
    fps: float = 0.0,
) -> list[str]:
    """Stage ``work_dir`` and return the ffmpeg argv that burns the captions.

    With ``matte_path`` (a greyscale person matte from ``engine.matte``)
    the captions go *behind* the speaker: the graph becomes a two-input
    ``-filter_complex`` (see ``matte.composite_filtergraph``) and ``fps``
    must be the rate the matte was rendered at.

    Staging means: ``ass_path`` is copied to ``work_dir/captions.ass`` and
    the filtergraph is written to ``work_dir/filter.txt``. The argv refers
    to both by those relative names, so **it must be run with
    ``cwd=work_dir``** (``burn_captions`` does). It writes to
    ``part_path_for(output_path)``; renaming that onto ``output_path`` is
    the caller's job once ffmpeg has exited 0.

    ``fontsdir`` points libass at the bundled font directory (spec 7A.4:
    ``ash_captions.styles.fontsdir_arg()``) so a style referencing one of
    the ~24 bundled faces actually resolves to it, instead of falling
    back to whatever's installed system-wide -- silently, since libass
    substitutes rather than erroring. ``audio_codec``/``width``/``height``
    come from ``probe_video`` when known.
    """
    ass_path = Path(ass_path)
    if not ass_path.is_file():
        raise BurnInError(f"Subtitle file not found: {ass_path}")
    video_path = Path(os.path.abspath(video_path))
    output_path = Path(os.path.abspath(output_path))
    work_dir = Path(os.path.abspath(work_dir))

    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ass_path, work_dir / CAPTIONS_FILENAME)
    caption_filter = build_filtergraph(fontsdir=fontsdir, punch_filter=None)
    if matte_path is not None:
        from .matte import composite_filtergraph

        if width <= 0 or height <= 0:
            raise BurnInError("Captions behind the speaker need the video's frame size (probe failed)")
        graph = composite_filtergraph(
            caption_filter=caption_filter, width=width, height=height, fps=fps, punch_filter=punch_filter
        )
        inputs = ["-i", str(video_path), "-i", str(Path(os.path.abspath(matte_path)))]
        maps = ["-map", "[out]", "-map", "0:a:0?"]
        filter_args = [filter_file_option(ffmpeg_path, complex_graph=True), FILTER_SCRIPT_FILENAME]
    else:
        graph = build_filtergraph(fontsdir=fontsdir, punch_filter=punch_filter)
        inputs = ["-i", str(video_path)]
        # Exactly the first video and (if present) first audio stream: a
        # camera file's data/timecode tracks would otherwise fail the mux
        # and a second audio track would be picked by "best", not "first".
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        filter_args = [filter_file_option(ffmpeg_path), FILTER_SCRIPT_FILENAME]
    (work_dir / FILTER_SCRIPT_FILENAME).write_text(graph, encoding="utf-8")

    encoder = select_video_encoder(ffmpeg_path, use_nvenc=use_nvenc)

    return [
        _executable(ffmpeg_path),
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        *inputs,
        *maps,
        *filter_args,
        *encoder_args(encoder, width=width, height=height),
        *audio_args(audio_codec),
        "-progress", "pipe:1",
        "-nostats",
        str(part_path_for(output_path)),
    ]


# ---------------------------------------------------------------------------
# running a burn
# ---------------------------------------------------------------------------


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass  # a leftover .part is a nuisance, not a failure to report


def _probe_best_effort(video_path: Path, ffmpeg_path: Path | str) -> VideoInfo | None:
    """The source's properties if ffprobe can supply them. A probe failure
    of any kind means "unknown", never "cannot burn"."""
    try:
        return probe_video(video_path, ffprobe_path=ffprobe_beside(ffmpeg_path))
    except (ProbeError, Exception):  # noqa: BLE001
        return None


@contextmanager
def _work_directory(work_dir: Path | str | None) -> Iterator[Path]:
    """A caller-supplied directory (left in place) or a fresh temporary
    one (removed afterwards). System temp is deliberate: short paths, and
    nothing of ours is left next to the client's deliverable."""
    if work_dir is not None:
        path = Path(work_dir)
        path.mkdir(parents=True, exist_ok=True)
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="ash-burn-") as temp:
        yield Path(temp)


def burn_captions(
    video_path: Path | str,
    ass_path: Path | str,
    output_path: Path | str,
    *,
    duration_seconds: float,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    use_nvenc: bool | None = None,
    fontsdir: Path | str | None = None,
    punch_filter: str | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    video_info: VideoInfo | None = None,
    work_dir: Path | str | None = None,
    matte_path: Path | str | None = None,
) -> Path:
    """Burn ``ass_path``'s captions into ``video_path``, writing an MP4.

    ``matte_path`` (from ``engine.matte.render_matte``) puts the captions
    behind the speaker; see ``build_burn_command``.

    Args:
        duration_seconds: total video duration, used to turn ffmpeg's raw
            ``out_time`` into a percentage. Callers get this from ffprobe
            or from an earlier step in the pipeline.
        use_nvenc: force NVENC on (True) or off (False); ``None`` (default)
            auto-detects via ``detect_nvenc()`` and a test encode.
        fontsdir: directory libass should search for bundled fonts (spec
            7A.4) -- see ``build_burn_command``.
        on_progress: called with a 0-100 float as ffmpeg reports progress.
        should_stop: polled on every progress line; True kills ffmpeg,
            removes the part file and raises ``BurnCancelled``.
        video_info: the source's ``probe_video`` result (audio codec and
            frame size). Probed here, best effort, when not given.
        work_dir: where ``captions.ass`` and ``filter.txt`` are staged;
            a temporary directory when ``None``.

    Raises:
        BurnCancelled: ``should_stop`` returned True.
        BurnInError: the video or ass file is missing, ffmpeg cannot be
            launched, or ffmpeg exits non-zero. In every case no file is
            left under ``output_path``'s name that was not there before.
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
    if video_info is None:
        video_info = _probe_best_effort(video_path, ffmpeg_path)

    part_path = part_path_for(output_path)
    with _work_directory(work_dir) as staging:
        args = build_burn_command(
            video_path,
            ass_path,
            output_path,
            work_dir=staging,
            ffmpeg_path=ffmpeg_path,
            use_nvenc=use_nvenc,
            fontsdir=fontsdir,
            punch_filter=punch_filter,
            audio_codec=video_info.audio_codec if video_info else None,
            width=video_info.width if video_info else 0,
            height=video_info.height if video_info else 0,
            matte_path=matte_path,
            fps=video_info.fps if video_info else 0.0,
        )
        try:
            run = run_ffmpeg(
                args,
                cwd=staging,
                duration_seconds=duration_seconds,
                on_progress=on_progress,
                should_stop=should_stop,
            )
        except OSError as exc:
            _discard(part_path)
            raise BurnInError(f"Failed to launch ffmpeg at {ffmpeg_path}: {exc}") from exc
        except BaseException:
            _discard(part_path)
            raise

    if run.cancelled:
        _discard(part_path)
        raise BurnCancelled(
            f"Burn-in of {video_path.name} was cancelled",
            stderr=run.stderr,
            returncode=run.returncode,
        )
    if run.returncode != 0:
        _discard(part_path)
        raise BurnInError(
            f"ffmpeg failed burning captions into {video_path.name} (exit {run.returncode})",
            stderr=run.stderr,
            returncode=run.returncode,
        )

    try:
        os.replace(part_path, output_path)
    except OSError as exc:
        _discard(part_path)
        raise BurnInError(
            f"ffmpeg finished but the result could not be moved to {output_path}: {exc}"
        ) from exc
    return output_path
