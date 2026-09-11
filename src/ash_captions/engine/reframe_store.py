"""Keeping a crop plan, and letting an editor correct it.

The measurement that makes this module worth having: on the studio's own
interview, 10 of 24 shots held more than one person, so 42% of the reel
was a *choice* the software made with no way to hear about it. The
default -- the largest person by matte mass -- is consistently the guest
and never the interviewer, and nothing in the pipeline listens to the
audio, so it is a guess about framing and not a claim about who is
speaking.

The correction is cheap because of what the plan already carries.
``CropWindow.candidates`` holds the x of every person the matte found in
that window, so re-aiming a window at a different one needs no footage,
no model and no second scan: it is arithmetic on a saved plan. That is
why the plan is written beside the burn as ``<stem>.reframe.json`` and
why a corrected re-burn is *faster* than the first one rather than
slower -- it skips the scan entirely.

``apply_overrides`` is pure and lives here rather than in ``reframe.py``
for a reason that is more than line count: everything in this module is
about a plan that already exists and a person changing their mind about
it, where ``reframe.py`` is about deciding one from measurements.
"""

from __future__ import annotations

import json
from pathlib import Path

from .reframe import CropPlan, CropWindow, ReframeError, clamp_centre

# Beside the .ass and the .srt, named off the same stem. Kept next to the
# deliverable rather than in a temp dir so re-opening a job a week later
# still offers the corrections.
PLAN_SUFFIX = ".reframe.json"

# Bumped if the shape below ever changes. A plan written by an older
# version is ignored rather than half-read: re-scanning costs ~30s and
# guessing at a stale shape costs a wrong deliverable.
PLAN_VERSION = 1


def plan_path_for(output_path: Path | str) -> Path:
    """``<dir>/<stem>.reframe.json`` for a job's output stem."""
    output_path = Path(output_path)
    return output_path.with_name(output_path.stem + PLAN_SUFFIX)


def apply_overrides(plan: CropPlan, overrides: dict[int, int] | None) -> CropPlan:
    """Re-aim chosen windows at a different person, from the plan alone.

    ``overrides`` maps a window index to an index into that window's
    ``candidates``. An entry naming a window or a candidate that does not
    exist is ignored rather than raising: the plan on disk and the page
    that edited it can disagree after a re-scan, and a stale correction
    should degrade to "the default framing", not to a failed burn.
    """
    if not overrides:
        return plan

    windows = list(plan.windows)
    for index, choice in overrides.items():
        if not (0 <= index < len(windows)):
            continue
        window = windows[index]
        if not (0 <= choice < len(window.candidates)):
            continue
        windows[index] = CropWindow(
            start=window.start,
            end=window.end,
            centre=clamp_centre(
                window.candidates[choice],
                crop_width=plan.crop_width,
                source_width=plan.source_width,
            ),
            chosen=choice,
            candidates=window.candidates,
        )
    return CropPlan(
        source_width=plan.source_width,
        source_height=plan.source_height,
        crop_width=plan.crop_width,
        crop_height=plan.crop_height,
        windows=tuple(windows),
    )


def plan_to_dict(plan: CropPlan) -> dict:
    return {
        "version": PLAN_VERSION,
        "source_width": plan.source_width,
        "source_height": plan.source_height,
        "crop_width": plan.crop_width,
        "crop_height": plan.crop_height,
        "windows": [
            {
                "start": round(w.start, 3),
                "end": round(w.end, 3),
                "centre": round(w.centre, 2),
                "chosen": w.chosen,
                "candidates": [round(c, 2) for c in w.candidates],
            }
            for w in plan.windows
        ],
    }


def plan_from_dict(data: dict) -> CropPlan:
    """Rebuild a plan from its stored form.

    Raises ``ReframeError`` on anything that is not a plan of the current
    version, including a hand-edited file: the caller's fallback is to
    scan again, which is always correct and merely slower.
    """
    if not isinstance(data, dict) or data.get("version") != PLAN_VERSION:
        raise ReframeError("not a crop plan of a version this build understands")
    try:
        windows = tuple(
            CropWindow(
                start=float(w["start"]),
                end=float(w["end"]),
                centre=float(w["centre"]),
                chosen=None if w.get("chosen") is None else int(w["chosen"]),
                candidates=tuple(float(c) for c in w.get("candidates", ())),
            )
            for w in data["windows"]
        )
        plan = CropPlan(
            source_width=int(data["source_width"]),
            source_height=int(data["source_height"]),
            crop_width=int(data["crop_width"]),
            crop_height=int(data["crop_height"]),
            windows=windows,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReframeError(f"the stored crop plan is not readable: {exc}") from exc
    if not plan.windows:
        raise ReframeError("the stored crop plan has no windows")
    return plan


def save_plan(plan: CropPlan, output_path: Path | str) -> Path:
    path = plan_path_for(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan_to_dict(plan), indent=2), encoding="utf-8")
    return path


def load_plan(output_path: Path | str) -> CropPlan | None:
    """The saved plan, or None when there is not a usable one.

    Every failure -- missing, unreadable, a version this build does not
    know -- comes back as None, because the caller's answer to all of
    them is the same: scan the footage again.
    """
    path = plan_path_for(output_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return plan_from_dict(data)
    except ReframeError:
        return None


def matches_source(plan: CropPlan, width: int, height: int) -> bool:
    """Whether a saved plan was made for footage of this shape.

    A plan holds source pixels, so reusing one made for a different frame
    size would crop the wrong part of the picture. Re-encoded or replaced
    footage is exactly the case this catches.
    """
    return plan.source_width == width and plan.source_height == height
