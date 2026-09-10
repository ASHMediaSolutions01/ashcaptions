"""Landscape to 9:16: decide where to crop, shot by shot.

Every short-form competitor turns a 1920x1080 interview into a vertical
reel. The hard part is not the crop -- ffmpeg has done that since 2000 --
it is deciding *where* it should sit, second by second, without the
result sliding around like a security camera.

Everything below is shaped by measurements on the studio's own 4:48
Spanish interview (1920x1080), taken before any of it was designed:

* **Inside a shot, nobody moves.** The matte's centroid drifts 13-31 px
  over twelve seconds, in a frame 1920 px wide. So the crop is decided
  once per shot and *held*. There is no path to smooth, and no jitter to
  filter, because there is no per-frame tracking at all.

* **The centroid is the steadiest estimator.** Of three tried -- mass
  centroid, midpoint of the blob's extent, and the centroid of the top
  third ("the head") -- the head was four times noisier (56-131 px of
  drift against 13-31 px), not better. The obvious guess was wrong, so
  ``subject_centre`` uses mass.

* **A centroid over the whole matte is useless on a two-shot.** The two
  people sat 1167 px apart and a 9:16 crop of a 1080-tall frame is 608
  px, so they cannot both fit; the centroid of both lands in the empty
  sofa between them, framing nobody. The alpha is therefore split into
  *blobs* first, and one of them is chosen.

* **The choice is common.** 10 of 24 shots had more than one person --
  42% of the reel. It is not a rare corner, so ``choose_subject`` takes
  an explicit override: an editor must be able to say "the other one".

* **Sampling is what makes this affordable.** Because nobody moves, a
  handful of frames per shot is enough: 120 inferences for the whole
  clip (~3.6 s) against 8658 (~260 s) to matte every frame.

* **Cut detection misses dissolves.** ffmpeg's scene score found 24 hard
  cuts, but the title card cross-dissolving into the first two-shot at
  ~5.5 s produced no spike at *any* threshold down to 0.10, because each
  frame barely differs from the last. A shot list from the scene score
  alone would hold one framing across two unrelated pictures, so
  ``split_on_subject_change`` subdivides a shot when its own samples
  disagree about where -- or whether -- the subject is.

* **Some frames contain no person at all** (5.5 s of this clip: 0.00% of
  the frame above half alpha). There is nothing to centre on, so the
  crop falls back to the middle of the frame rather than to whatever
  noise the matte returned -- which was 216 px off centre.

This module is pure: it takes sampled measurements and returns a plan.
Producing the samples (ffmpeg + RVM) belongs to the caller, and turning
the plan into pixels is ``build_crop_filter``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

# The same balanced sum punch-in uses, and for the same discovered reason:
# ffmpeg's expression parser refuses to recurse past 100 levels, so a flat
# chain of about 80 '+' terms fails to parse. One implementation, so a fix
# to one cannot leave the other broken.
from .punch import _balanced_sum

# A 9:16 reel. Kept as a Fraction so the crop width of an odd source
# height is still exact before it is rounded to an even number of pixels.
PORTRAIT = Fraction(9, 16)

# Column mass below this fraction of the frame's peak is background
# spill, not a person. Measured floor: on the two-shot the gap between
# the two people sat under 5% of peak while both people stayed above 40%.
BLOB_FLOOR = 0.15

# A run of columns narrower than this is noise -- a hand at the frame
# edge, a chair leg the matte liked. As a fraction of frame width so it
# survives a change of working resolution.
MIN_BLOB_WIDTH = 0.012

# Two samples in the same shot are "the same framing" while the subject
# is within this fraction of the frame width. The measured within-shot
# drift was at most 31 px in 1920 (1.6%); the dissolve moved the subject
# by 450 px (23%). Anything between those two is a judgement call, and
# 6% sits an order of magnitude away from the noise.
SAME_FRAMING = 0.06

# A shot shorter than this cannot carry its own framing decision: the
# crop would change twice in less time than a viewer can follow.
MIN_SHOT_SECONDS = 0.5


class ReframeError(Exception):
    """The footage cannot be reframed to the requested shape."""


@dataclass(frozen=True, slots=True)
class Blob:
    """One person-shaped run of matte columns, in source pixels."""

    centre: float
    width: float
    mass: float


@dataclass(frozen=True, slots=True)
class Sample:
    """What the matte said at one instant."""

    time: float
    blobs: tuple[Blob, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.blobs


@dataclass(frozen=True, slots=True)
class Shot:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class CropWindow:
    """Where the crop sits for one stretch of time, in source pixels.

    ``centre`` is the x of the middle of the crop. ``chosen`` is the index
    of the blob it framed, or None when the frame held nobody and the
    crop fell back to the middle -- the editor's "who is this following?"
    needs to be able to say "nobody".
    """

    start: float
    end: float
    centre: float
    chosen: int | None = None
    candidates: tuple[float, ...] = ()

    @property
    def contested(self) -> bool:
        """More than one person was on screen, so this was a choice."""
        return len(self.candidates) > 1


@dataclass(frozen=True, slots=True)
class CropPlan:
    source_width: int
    source_height: int
    crop_width: int
    crop_height: int
    windows: tuple[CropWindow, ...] = field(default_factory=tuple)

    @property
    def contested_windows(self) -> tuple[CropWindow, ...]:
        return tuple(w for w in self.windows if w.contested)

    @property
    def detail_width(self) -> int:
        """How many real source pixels the reel is built from.

        A 1080p landscape source gives a 606 px-wide crop, so a 1080-wide
        reel is a 1.8x upscale and carries a third of the detail the
        source had. That is what every tool in this category does, but it
        is the reason to shoot 4K when the reel is the deliverable.
        """
        return self.crop_width


def crop_size(width: int, height: int, ratio: Fraction = PORTRAIT) -> tuple[int, int]:
    """The largest ``ratio`` rectangle that fits inside ``width`` x ``height``.

    Both sides are rounded down to even numbers: yuv420p subsamples
    chroma by two, and ffmpeg refuses an odd crop.
    """
    if width <= 0 or height <= 0:
        raise ReframeError("the source has no size to crop")
    by_height = int(height * ratio)
    if by_height <= width:
        w, h = by_height, height
    else:
        w, h = width, int(width / ratio)
    w = max(2, w - (w % 2))
    h = max(2, h - (h % 2))
    return w, h


def reel_size(width: int, height: int, ratio: Fraction = PORTRAIT) -> tuple[int, int]:
    """What the reel is delivered at: 1080x1920, but never a wild upscale.

    A 1080p landscape source crops to 606px of real detail, so a 1080-wide
    reel is a 1.8x upscale -- which is what every tool in this category
    ships, and the reason to shoot 4K when the reel is the deliverable.
    Doubling is where that stops being a trade and starts being mush, so a
    small source is delivered smaller rather than blown up to fit a number.
    """
    crop_w, crop_h = crop_size(width, height, ratio)
    out_h = min(1920, crop_h * 2)
    out_w = int(out_h * ratio)
    return (max(2, out_w - (out_w % 2)), max(2, out_h - (out_h % 2)))


def blobs_from_columns(
    columns: list[float] | tuple[float, ...],
    *,
    source_width: int,
    floor: float = BLOB_FLOOR,
    min_width: float = MIN_BLOB_WIDTH,
) -> tuple[Blob, ...]:
    """Split one frame's matte column sums into people.

    ``columns`` is the per-column sum of the alpha plane, at whatever
    width inference ran at; results come back in *source* pixels so the
    rest of the module never has to know the working resolution.
    """
    if not columns:
        return ()
    peak = max(columns)
    if peak <= 0:
        return ()
    scale = source_width / float(len(columns))
    cutoff = peak * floor
    min_cols = max(1, int(round(min_width * len(columns))))

    blobs: list[Blob] = []
    start: int | None = None
    for i, value in enumerate(list(columns) + [0.0]):
        if value > cutoff and start is None:
            start = i
        elif value <= cutoff and start is not None:
            if i - start >= min_cols:
                blobs.append(_blob(columns, start, i, scale))
            start = None
    return tuple(blobs)


def _blob(columns, start: int, stop: int, scale: float) -> Blob:
    segment = columns[start:stop]
    mass = sum(segment)
    if mass > 0:
        centre = sum(v * (start + i) for i, v in enumerate(segment)) / mass
    else:
        centre = (start + stop - 1) / 2.0
    return Blob(centre=centre * scale, width=(stop - start) * scale, mass=float(mass))


def subject_centre(blob: Blob) -> float:
    """Where to aim the crop for one person.

    The mass centroid, measured steadier than either the midpoint of the
    blob's extent or the centroid of its top third. See the module note.
    """
    return blob.centre


def choose_subject(
    blobs: tuple[Blob, ...] | list[Blob], *, prefer: int | None = None
) -> int | None:
    """Which person the crop follows, as an index into ``blobs``.

    ``prefer`` is the editor's override and wins whenever it points at a
    blob that exists. Otherwise the largest by mass -- on the measured
    footage that is consistently the person nearest the camera. It is
    *not* a claim about who is speaking: nothing here listens. Telling
    the speaker apart needs diarisation, which is a separate model and a
    separate download, so the honest default is "the biggest person" plus
    a control that changes it.
    """
    if not blobs:
        return None
    if prefer is not None and 0 <= prefer < len(blobs):
        return prefer
    return max(range(len(blobs)), key=lambda i: blobs[i].mass)


def clamp_centre(centre: float, *, crop_width: int, source_width: int) -> float:
    """Keep the crop inside the frame.

    A subject near the edge would otherwise ask for a crop that hangs off
    it. Sliding it back in is right: the alternative -- padding with
    black -- puts bars in a reel that is meant to fill a phone.
    """
    half = crop_width / 2.0
    if crop_width >= source_width:
        return source_width / 2.0
    return min(max(centre, half), source_width - half)


def shots_from_cuts(
    cuts: list[float] | tuple[float, ...],
    duration: float,
    *,
    minimum: float = MIN_SHOT_SECONDS,
) -> tuple[Shot, ...]:
    """Turn cut timestamps into shots, dropping ones too short to matter."""
    bounds = [0.0] + [c for c in sorted(cuts) if 0.0 < c < duration] + [float(duration)]
    shots = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        if end - start >= minimum:
            shots.append(Shot(start=start, end=end))
    if not shots:
        shots = [Shot(start=0.0, end=float(duration))]
    return tuple(shots)


def split_on_subject_change(
    shot: Shot,
    samples: list[Sample] | tuple[Sample, ...],
    *,
    source_width: int,
    tolerance: float = SAME_FRAMING,
    minimum: float = MIN_SHOT_SECONDS,
) -> tuple[tuple[Shot, tuple[Sample, ...]], ...]:
    """Subdivide a shot whose own samples disagree about the subject.

    This exists because cut detection misses dissolves: the measured clip
    fades a white title card into a two-shot over about a second, and
    ffmpeg's scene score never spikes, at any threshold. The samples do
    notice -- half of them find nobody and half find two people -- so the
    samples, not the scene score, decide where the framing changes.
    """
    ordered = tuple(sorted(samples, key=lambda s: s.time))
    if len(ordered) < 2:
        return ((shot, ordered),)

    limit = tolerance * source_width
    groups: list[list[Sample]] = [[ordered[0]]]
    for sample in ordered[1:]:
        if _same_framing(groups[-1][-1], sample, limit):
            groups[-1].append(sample)
        else:
            groups.append([sample])
    if len(groups) == 1:
        return ((shot, ordered),)

    pieces: list[tuple[Shot, tuple[Sample, ...]]] = []
    start = shot.start
    for index, group in enumerate(groups):
        if index == len(groups) - 1:
            end = shot.end
        else:
            # The change happened somewhere between the last sample of
            # this group and the first of the next; without a finer scan
            # the midpoint is the best available estimate, and a dissolve
            # makes any instant in that span defensible.
            end = (group[-1].time + groups[index + 1][0].time) / 2.0
        pieces.append((Shot(start=start, end=end), tuple(group)))
        start = end

    merged = _merge_short(pieces, minimum)
    return tuple(merged)


def _same_framing(a: Sample, b: Sample, limit: float) -> bool:
    if a.empty != b.empty:
        return False
    if a.empty and b.empty:
        return True
    if len(a.blobs) != len(b.blobs):
        return False
    pairs = zip(sorted(x.centre for x in a.blobs),
                sorted(x.centre for x in b.blobs), strict=True)
    return all(abs(x - y) <= limit for x, y in pairs)


def _merge_short(pieces, minimum):
    """Fold a too-short piece into its neighbour rather than emit a flash."""
    out = list(pieces)
    i = 0
    while len(out) > 1 and i < len(out):
        shot, samples = out[i]
        if shot.duration >= minimum:
            i += 1
            continue
        if i + 1 < len(out):
            nxt_shot, nxt_samples = out[i + 1]
            out[i + 1] = (Shot(shot.start, nxt_shot.end), samples + nxt_samples)
            out.pop(i)
        else:
            prev_shot, prev_samples = out[i - 1]
            out[i - 1] = (Shot(prev_shot.start, shot.end), prev_samples + samples)
            out.pop(i)
            i = max(0, i - 1)
    return out


def plan_crops(
    shots: list[Shot] | tuple[Shot, ...],
    samples_by_shot: list[list[Sample]] | tuple[tuple[Sample, ...], ...],
    *,
    source_width: int,
    source_height: int,
    ratio: Fraction = PORTRAIT,
    overrides: dict[int, int] | None = None,
) -> CropPlan:
    """One held crop per shot, from that shot's samples.

    ``overrides`` maps a window index to the blob an editor picked. The
    plan is rebuilt from the same samples when one changes, so correcting
    a shot never re-runs the matte.
    """
    crop_w, crop_h = crop_size(source_width, source_height, ratio)
    overrides = overrides or {}
    windows: list[CropWindow] = []

    for shot, samples in zip(shots, samples_by_shot, strict=True):
        pieces = split_on_subject_change(shot, samples, source_width=source_width)
        for piece_shot, piece_samples in pieces:
            blobs = _representative_blobs(piece_samples)
            index = choose_subject(blobs, prefer=overrides.get(len(windows)))
            if index is None:
                centre = source_width / 2.0
            else:
                centre = subject_centre(blobs[index])
            windows.append(
                CropWindow(
                    start=piece_shot.start,
                    end=piece_shot.end,
                    centre=clamp_centre(centre, crop_width=crop_w, source_width=source_width),
                    chosen=index,
                    candidates=tuple(b.centre for b in blobs),
                )
            )

    return CropPlan(
        source_width=source_width,
        source_height=source_height,
        crop_width=crop_w,
        crop_height=crop_h,
        windows=tuple(windows),
    )


def _representative_blobs(samples: tuple[Sample, ...]) -> tuple[Blob, ...]:
    """One blob list standing for the whole stretch.

    The sample with the most people wins, so a frame where someone was
    briefly occluded does not erase them from the shot; among those, the
    middle one in time, which is furthest from either boundary.
    """
    populated = [s for s in samples if s.blobs]
    if not populated:
        return ()
    most = max(len(s.blobs) for s in populated)
    candidates = [s for s in populated if len(s.blobs) == most]
    return candidates[len(candidates) // 2].blobs


def build_crop_filter(plan: CropPlan, *, output: tuple[int, int] | None = None) -> str:
    """The ffmpeg filter that carries the plan out.

    ``output`` scales the crop to a delivery size -- a 1080p landscape
    source crops to 606x1080, and a reel is 1080x1920. The scale belongs
    here rather than in the caller because the captions are drawn *after*
    it, so the .ass has to be written for the size this returns.

    One ``crop`` whose ``x`` is an expression of the frame's own timestamp,
    exactly as ``punch.build_punch_filter`` does it and for the same
    reason: a variable-frame-rate phone recording keeps its timestamps,
    where anything driven by a frame counter drifts over a long file.

    The offset is a sum of ``between(t,a,b)*x`` terms rather than a ramp:
    a shot change is a cut, and the crop should arrive at its new place
    on the same frame the picture does. The sum is balanced because
    ffmpeg's expression parser refuses to recurse past 100 levels, which
    a flat chain of about 80 terms already breaks (see ``punch``).
    """
    if not plan.windows:
        raise ReframeError("the plan has no windows, so there is nothing to crop")

    default = plan.source_width / 2.0
    terms = []
    for window in plan.windows:
        offset = window.centre - plan.crop_width / 2.0
        terms.append(f"between(t,{window.start:.3f},{window.end:.3f})*{offset:.2f}")
    # Frames outside every window (rounding at the very end of the file)
    # fall back to the middle rather than to x=0, which would slam the
    # crop to the left edge for the last frame or two.
    covered = "+".join(
        f"between(t,{w.start:.3f},{w.end:.3f})" for w in plan.windows
    )
    fallback = f"(1-min(1,{_balanced_sum([covered])}))*{default - plan.crop_width / 2.0:.2f}"
    expression = f"{_balanced_sum(terms)}+{fallback}"
    y = (plan.source_height - plan.crop_height) / 2.0
    graph = (
        f"crop=w={plan.crop_width}:h={plan.crop_height}"
        f":x='max(0,min({plan.source_width - plan.crop_width},{expression}))'"
        f":y={y:.0f}"
    )
    if output:
        out_w, out_h = output
        if out_w <= 0 or out_h <= 0:
            raise ReframeError("the output size must be positive")
        graph += f",scale={out_w}:{out_h}:flags=lanczos"
    return graph
