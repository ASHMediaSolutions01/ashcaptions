"""Punch-in: zoom the footage on chosen words during burn-in.

Captions tell the viewer what is being said; a punch-in tells them it
matters. On a talking-head reel where the framing never moves, this is
what separates an edited video from a captioned one.

The trigger is deliberately predictable rather than clever. Beat or
energy detection sounds impressive and produces "why did it zoom there"
moments; punching on the first word of a sentence gives a rhythm an
editor can look at once and trust for the next fifty videos. Keywords
are the opt-in for emphasis that matters to a particular client.

Implementation note: the zoom is a per-frame ``scale`` (sized by an
expression of the frame's timestamp) followed by a fixed-size centre
``crop``, so the output size is constant while every frame keeps its
input timestamp (see ``build_punch_filter``). The filter is placed
*before* the subtitle filter so captions are drawn on top at their true
size, rather than being magnified along with the picture.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .transcribe import Word

# A punch shorter than this is a flicker rather than an emphasis, and one
# longer stops reading as a punch and starts reading as a different shot.
MIN_DURATION_SECONDS = 0.25
MAX_DURATION_SECONDS = 4.0

SENTENCE_END = (".", "!", "?")


class PunchMode(str, Enum):
    OFF = "off"
    SENTENCE = "sentence"
    KEYWORD = "keyword"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class PunchMoment:
    """One zoom window, in seconds from the start of the video."""

    start: float
    end: float
    trigger: str  # "sentence" or "keyword" -- for logging and tests

    @property
    def duration(self) -> float:
        return self.end - self.start


def is_sentence_start(index: int, words: list[Word], sentence_gap: float) -> bool:
    """True when this word begins a new thought.

    Public because ``engine.sfx`` fires a sound on exactly the same
    moments a punch-in zooms on; two copies of this rule would drift and
    the two effects would stop landing together."""
    if index == 0:
        return True
    previous = words[index - 1]
    if previous.text.rstrip().endswith(SENTENCE_END):
        return True
    # A long pause starts a new thought even without punctuation, which is
    # common in speech Whisper transcribes without a full stop.
    return (words[index].start - previous.end) >= sentence_gap


_is_sentence_start = is_sentence_start  # the name this had before v0.7


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def select_punch_moments(
    words: list[Word] | tuple[Word, ...],
    *,
    mode: PunchMode | str = PunchMode.SENTENCE,
    keywords: tuple[str, ...] = (),
    duration: float = 1.2,
    min_spacing: float = 4.0,
    sentence_gap: float = 0.6,
    video_duration: float | None = None,
) -> list[PunchMoment]:
    """Choose when to punch in.

    ``min_spacing`` is the important one: punching on every sentence in
    fast dialogue is nauseating, so a moment is skipped when the previous
    punch started less than this many seconds ago.
    """
    mode = PunchMode(mode)
    if mode is PunchMode.OFF or not words:
        return []

    duration = max(MIN_DURATION_SECONDS, min(MAX_DURATION_SECONDS, duration))
    wanted = {_normalise(k) for k in keywords if _normalise(k)}
    word_list = list(words)

    moments: list[PunchMoment] = []
    last_start: float | None = None

    for index, word in enumerate(word_list):
        trigger: str | None = None
        if mode in (PunchMode.SENTENCE, PunchMode.BOTH) and is_sentence_start(
            index, word_list, sentence_gap
        ):
            trigger = "sentence"
        if (
            trigger is None
            and mode in (PunchMode.KEYWORD, PunchMode.BOTH)
            and _normalise(word.text) in wanted
        ):
            trigger = "keyword"
        if trigger is None:
            continue

        if last_start is not None and (word.start - last_start) < min_spacing:
            continue

        end = word.start + duration
        if video_duration is not None:
            end = min(end, video_duration)
        if (end - word.start) < MIN_DURATION_SECONDS:
            continue

        moments.append(PunchMoment(start=word.start, end=end, trigger=trigger))
        last_start = word.start

    return moments


def build_punch_filter(
    moments: list[PunchMoment] | tuple[PunchMoment, ...],
    *,
    zoom: float = 1.12,
    ease_seconds: float = 0.18,
) -> str | None:
    """Build the ``scale,crop`` punch-in chain, or ``None`` when there is
    nothing to do.

    Returning ``None`` rather than an identity filter matters: a filter
    pass that touches every frame costs time on a 90-minute 4K master,
    so a video with no punches should not pay for one.

    The chain is ``scale=w='iw*Z(t)':h='ih*ow/iw':eval=frame`` followed by
    ``crop=w=iw:h=ih:x='iw*(Z(t)-1)/2':y='x*ih/iw'``:

    * ``scale`` with ``eval=frame`` re-evaluates its size per frame from
      the frame's own timestamp ``t``, so the zoom follows *time*, not a
      frame counter, and every frame keeps its input timestamp. That is
      what keeps a variable-frame-rate phone recording in sync with its
      copied audio over 90 minutes -- ``zoompan`` regenerated timestamps
      from a constant ``fps`` and drifted on exactly those files.
    * Outside a punch ``Z(t)`` is exactly 1, and ``scale`` passes such
      frames through untouched.
    * ``crop``'s size is fixed when the graph is configured (``t`` is
      unset then, so ``scale`` reports the source size) and only its
      offset is re-evaluated per frame. Taking the size from the stream
      itself rather than from a probe means a phone video with a
      rotation tag -- which ffmpeg auto-rotates on decode, swapping the
      probed width and height -- cannot be cropped to the wrong shape.
      The offset is computed from the envelope rather than from
      ``crop``'s own ``in_w``, which is captured at config time and
      would otherwise leave every punched frame cropped top-left.
    """
    if not moments:
        return None

    zoom = max(1.0, zoom)
    if zoom == 1.0:
        return None

    ease = max(0.01, ease_seconds)
    terms = []
    for moment in moments:
        # Ramp in over `ease`, hold, ramp out over `ease`, and zero outside
        # the window. Without the ramps the zoom is a hard jump, which reads
        # as a glitch rather than an emphasis on a talking head.
        terms.append(
            f"between(t,{moment.start:.3f},{moment.end:.3f})"
            f"*min(1,(t-{moment.start:.3f})/{ease:.3f})"
            f"*min(1,({moment.end:.3f}-t)/{ease:.3f})"
        )

    envelope = _balanced_sum(terms)
    excess = f"{zoom - 1:.4f}*max(0,{envelope})"  # Z(t) - 1

    # ``crop`` cannot see the incoming frame's size (its ``in_w`` is fixed
    # at graph config), so the centre offset is computed from the same
    # envelope: the scaled frame is ``iw*Z`` wide, the crop ``iw`` wide,
    # so it starts at ``iw*(Z-1)/2``. ``h`` and ``y`` derive from the
    # already-computed ``ow``/``x`` so a 700-moment envelope is walked
    # twice per frame, not four times.
    return (
        f"scale=w='iw*(1+{excess})'"
        ":h='ih*ow/iw'"
        ":eval=frame"
        f",crop=w=iw:h=ih:x='iw*({excess})/2':y='x*ih/iw'"
    )


# ffmpeg's expression parser recurses once per ``+`` and refuses to go
# deeper than 100 levels, so a flat ``a+b+c+...`` envelope fails to parse
# somewhere past 80 terms -- a 7-minute talk at 5s spacing. Summing in a
# balanced tree keeps the depth logarithmic: ~40 levels for 900 moments.
_SUM_LEAF_TERMS = 16


def _balanced_sum(terms: list[str]) -> str:
    if len(terms) <= _SUM_LEAF_TERMS:
        return "+".join(terms)
    middle = len(terms) // 2
    return f"({_balanced_sum(terms[:middle])})+({_balanced_sum(terms[middle:])})"


def build_zoompan_filter(
    moments: list[PunchMoment] | tuple[PunchMoment, ...],
    *,
    width: int,
    height: int,
    fps: float,
    zoom: float = 1.12,
    ease_seconds: float = 0.18,
) -> str | None:
    """Compatibility name for ``build_punch_filter``.

    The punch used to be a ``zoompan`` filter that had to be told the
    output size and frame rate. It no longer is (see ``build_punch_filter``
    for why), so ``width``/``height`` are only validated and ``fps`` is
    ignored; callers that already pass them keep working unchanged.
    """
    if moments and (width <= 0 or height <= 0):
        raise ValueError("width and height must be positive to build a zoom filter")
    del fps  # the scale/crop chain follows frame timestamps, not a rate
    return build_punch_filter(moments, zoom=zoom, ease_seconds=ease_seconds)
