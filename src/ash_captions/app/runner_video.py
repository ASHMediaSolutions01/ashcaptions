"""The two stages that shape a job's *picture*: punch-in and reframing.

Both turn settings and probed geometry into one ffmpeg video filter for
the burn, and both are optional. They live outside ``runner.py`` because
that module sits at the 500-line ceiling the tests enforce, and because
this is a real seam: everything here answers "what shape is the video,
and where does it look?", and none of it knows how a burn is run.

They differ in one way that matters, and it is deliberate:

* **Punch-in degrades.** A failure to build it logs and burns without it.
  Captions are the deliverable; the zoom is a flourish.
* **Reframing fails the job.** An editor who asked for a 9:16 reel and
  silently got the landscape original back has been handed the wrong
  deliverable, and would only find out after posting it.

Two further rules are enforced here rather than in the engine, because
they are about what a *job* means rather than about geometry:

* **Reframing only happens on a burn.** The crop lives in the burned
  video; a .srt has no shape. Writing the .ass at reel size for a job
  that produces no reel would leave captions sized for a video that does
  not exist.
* **A source already the right shape is left alone.** Cropping a
  1080x1920 file to 9:16 is a no-op dressed up as a filter, and the extra
  decode pass is not free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ash_captions import engine

log = logging.getLogger(__name__)

# The scan is a decode pass plus a few hundred inferences -- comparable to
# the matte, and well short of a burn. It takes the front of the burn's
# progress span, the same way `behind_speaker` takes the first 40%.
SCAN_PROGRESS_SHARE = 0.25


@dataclass(frozen=True, slots=True)
class Reframe:
    """What the burn needs to turn a landscape source into a reel."""

    crop_filter: str
    output_size: tuple[int, int]
    plan: Any

    @property
    def contested(self) -> int:
        return len(self.plan.contested_windows)


def caption_play_res(job_options: Any, info: Any) -> tuple[int, int] | None:
    """The size the .ass must be written at: the reel's, or the source's.

    Called before the captions are rendered, and deliberately cheap: it is
    arithmetic on the probed frame size, not a scan. The plan is not
    needed to know how big the reel will be, only to know where the crop
    sits inside it. Returns None when the probe failed, which is the
    caller's existing "no PlayRes" case.
    """
    if info is None or getattr(info, "width", 0) <= 0 or getattr(info, "height", 0) <= 0:
        return None
    if wants_reframe(job_options):
        return engine.reel_size(info.width, info.height)
    return (info.width, info.height)


def wants_reframe(job_options: Any) -> bool:
    """True when this job asked for a reel and produces one."""
    return bool(getattr(job_options, "reframe", False)) and bool(
        getattr(job_options, "burn", False)
    )


def build_reframe(
    job_options: Any,
    video_path: Path,
    info: Any,
    *,
    duration_seconds: float,
    models_dir: Path | str,
    ffmpeg_path: Path | str,
    threads: int | None = None,
    overrides: dict[int, int] | None = None,
    output_path: Path | str | None = None,
    on_progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Reframe | None:
    """Return the crop for this job, or None when not reframing.

    ``output_path`` is where the deliverable goes; the plan is saved
    beside it so an editor can correct the framing later. When a saved
    plan is already there for footage of this shape, a correction reuses
    it and **skips the scan** -- the plan carries every candidate's
    position, so re-aiming a window is arithmetic, not measurement. That
    makes a corrected re-burn faster than the first one.

    Raises ``engine.ReframeScanError`` if the scan cannot run -- see the
    module note on why this is not degraded to a normal burn.
    """
    if not wants_reframe(job_options):
        return None
    if info is None or info.width <= 0 or info.height <= 0:
        raise engine.ReframeScanError(
            "Reframing to 9:16 needs the video's frame size, and ffprobe could not read it."
        )

    output_size = engine.reel_size(info.width, info.height)
    if (info.width, info.height) == output_size:
        # Already the shape being asked for. Cropping would be a no-op
        # dressed up as a filter, and the extra decode pass is not free.
        log.info("reframe: the source is already %dx%d; nothing to crop", *output_size)
        return None

    plan = _reuse_saved_plan(output_path, info) if overrides else None
    if plan is not None:
        log.info("reframe: reusing the saved plan and applying %d correction(s)", len(overrides))
        plan = engine.apply_overrides(plan, overrides)
        if on_progress is not None:
            on_progress(100.0)
    else:
        plan = engine.scan_reframe(
            video_path,
            width=info.width,
            height=info.height,
            duration_seconds=duration_seconds,
            models_dir=models_dir,
            ffmpeg_path=ffmpeg_path,
            threads=threads,
            overrides=overrides,
            on_progress=on_progress,
            should_stop=should_stop,
        )
    if output_path is not None:
        try:
            engine.save_plan(plan, output_path)
        except OSError:
            # The reel is the deliverable; losing the ability to correct
            # its framing later is not worth failing the burn over.
            log.warning("reframe: could not save the crop plan", exc_info=True)
    crop_filter = engine.build_crop_filter(plan, output=output_size)
    log.info(
        "reframe: %dx%d -> %dx%d over %d window(s); %d needed a choice between people",
        info.width, info.height, output_size[0], output_size[1],
        len(plan.windows), len(plan.contested_windows),
    )
    return Reframe(crop_filter=crop_filter, output_size=output_size, plan=plan)


def build_punch(
    settings: Any,
    words: tuple,
    info: Any,
    *,
    video_path: Path,
    ffmpeg_path: Path | str,
    probe: Callable[..., Any] | None = None,
) -> str | None:
    """The punch-in zoom filter, or None.

    Off unless the studio turned it on, because it changes how a client's
    video is framed and should never happen to footage silently. Any
    failure to build it degrades to a normal burn rather than losing the
    job -- see the module note.
    """
    if getattr(settings, "punch_mode", "off") == "off":
        return None
    try:
        geometry = info
        if geometry is None:
            probe_fn = probe or engine.probe_video
            geometry = probe_fn(video_path, ffprobe_path=engine.ffprobe_beside(ffmpeg_path))
        moments = engine.select_punch_moments(
            words,
            mode=settings.punch_mode,
            keywords=tuple(settings.punch_keywords),
            duration=settings.punch_duration_seconds,
            min_spacing=settings.punch_min_spacing_seconds,
            video_duration=geometry.duration_seconds,
        )
        # build_punch_filter is the timestamp-preserving scale/crop chain
        # and needs no geometry (it works in iw/ih and t);
        # build_zoompan_filter is the older zoompan form, which must be
        # told the output size and fps. Passing geometry to the new one
        # raised TypeError, which the guard below turned into a silent
        # "no punch-in" on every job.
        builder = getattr(engine, "build_punch_filter", None)
        if builder is not None:
            punch_filter = builder(moments, zoom=settings.punch_zoom)
        else:
            punch_filter = engine.build_zoompan_filter(
                moments,
                width=geometry.width,
                height=geometry.height,
                fps=geometry.fps,
                zoom=settings.punch_zoom,
            )
        log.info("punch-in: %d moment(s) at zoom %.2f", len(moments), settings.punch_zoom)
        return punch_filter
    except Exception:  # noqa: BLE001
        log.warning("punch-in unavailable; burning without it", exc_info=True)
        return None


def _reuse_saved_plan(output_path: Path | str | None, info: Any):
    """A saved plan for footage of this shape, or None to scan again.

    Every way this can fail -- no file, unreadable, a different frame
    size because the footage was replaced -- returns None, because the
    answer to all of them is the same: measure it again.
    """
    if output_path is None:
        return None
    plan = engine.load_plan(output_path)
    if plan is None:
        return None
    from ash_captions.engine.reframe_store import matches_source

    if not matches_source(plan, info.width, info.height):
        log.info(
            "reframe: the saved plan is for %dx%d but the footage is %dx%d; scanning again",
            plan.source_width, plan.source_height, info.width, info.height,
        )
        return None
    return plan


def build_stickers(
    style: Any, words: tuple, info: Any, *, keywords: tuple = (), duration_seconds: float
):
    """The look's emoji bursts, placed on its words -- or None.

    Reads the look, not the settings: emoji moved off settings.json in
    v0.9 for the reason ``schema.EmojiBursts`` gives. The keyword list is
    still the client's one shared list, handed in by the caller exactly
    as it is for sound and punch-in.

    Degrades like punch-in rather than failing like the reel: a sticker
    is a flourish on top of the deliverable, and a look asking for an
    emoji this build does not ship should cost the editor a picture, not
    a video.
    """
    block = getattr(style, "emoji", None)
    if block is None or not getattr(block, "enabled", False):
        return None
    try:
        from ash_captions import styles

        bursts = engine.select_bursts(
            words,
            trigger=block.trigger,
            emoji=block.emoji,
            keywords=keywords,
            min_spacing=block.min_spacing_seconds,
            video_duration=duration_seconds or None,
        )
        width = getattr(info, "width", 0) or 1080
        height = getattr(info, "height", 0) or 1920
        plan = engine.build_sticker_plan(
            bursts, styles.emoji_path, width=width, height=height
        )
        if plan is not None:
            log.info("emoji bursts: %d at %dpx", len(plan.bursts), plan.size_px)
        return plan
    except Exception:  # noqa: BLE001
        log.warning("emoji bursts unavailable; burning without them", exc_info=True)
        return None


def render_matte_first(
    video_path: Path,
    matte_path: Path,
    info: Any,
    *,
    settings: Any,
    duration_seconds: float,
    ffmpeg_path: Path,
    span: tuple[int, int],
    report: Callable[[int], None],
    should_stop: Any,
    on_stage: Callable[[str], None],
) -> int:
    """Render the person matte the "captions behind the speaker" burn needs.

    Returns the progress point the burn itself should start from: the
    matte is about the video's own length on a CPU, so it takes the first
    40% of the burn's span the way the reel scan takes the front of it.

    Fails the job rather than degrading, which is the same rule reframing
    follows and for the same reason: the editor asked for the effect, and
    quietly handing back a normal burn is handing back the wrong
    deliverable.
    """
    if info is None or getattr(info, "width", 0) <= 0:
        raise RuntimeError(
            "Captions behind the speaker need the video's frame size, and ffprobe could not read it."
        )
    start, end = span
    matte_end = start + round((end - start) * 0.4)
    on_stage("matte")
    model_path = engine.ensure_matte_model(settings.model_cache_dir, download=True)
    engine.render_matte(
        video_path,
        matte_path,
        model_path=model_path,
        width=info.width,
        height=info.height,
        fps=info.fps,
        duration_seconds=duration_seconds,
        ffmpeg_path=ffmpeg_path,
        threads=settings.cpu_threads,
        on_progress=lambda pct: report(round(start + (matte_end - start) * (pct / 100))),
        should_stop=should_stop,
    )
    on_stage("burn")
    return matte_end
