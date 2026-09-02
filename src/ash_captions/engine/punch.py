"""Punch-in: zoom the footage on chosen words during burn-in.

Captions tell the viewer what is being said; a punch-in tells them it
matters. On a talking-head reel where the framing never moves, this is
what separates an edited video from a captioned one.

The trigger is deliberately predictable rather than clever. Beat or
energy detection sounds impressive and produces "why did it zoom there"
moments; punching on the first word of a sentence gives a rhythm an
editor can look at once and trust for the next fifty videos. Keywords
are the opt-in for emphasis that matters to a particular client.

Implementation note: a filter must emit a constant frame size, so an
animated ``crop`` is not an option -- its ``w``/``h`` would change per
frame, which ffmpeg rejects. ``zoompan`` exists for exactly this: it
zooms while emitting a fixed ``s=WxH``. The filter is placed *before*
the subtitle filter so captions are drawn on top at their true size,
rather than being magnified along with the picture.
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


def _is_sentence_start(index: int, words: list[Word], sentence_gap: float) -> bool:
    if index == 0:
        return True
    previous = words[index - 1]
    if previous.text.rstrip().endswith(SENTENCE_END):
        return True
    # A long pause starts a new thought even without punctuation, which is
    # common in speech Whisper transcribes without a full stop.
    return (words[index].start - previous.end) >= sentence_gap


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
        if mode in (PunchMode.SENTENCE, PunchMode.BOTH) and _is_sentence_start(
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


def build_zoompan_filter(
    moments: list[PunchMoment] | tuple[PunchMoment, ...],
    *,
    width: int,
    height: int,
    fps: float,
    zoom: float = 1.12,
    ease_seconds: float = 0.18,
) -> str | None:
    """Build the ``zoompan`` filter, or ``None`` when there is nothing to do.

    Returning ``None`` rather than an identity filter matters: a zoompan
    pass re-encodes every frame even at zoom 1.0, so a video with no
    punches should not pay for one.
    """
    if not moments:
        return None
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive to build a zoom filter")

    effective_fps = fps if fps > 0 else 30.0
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
            f"between(it,{moment.start:.3f},{moment.end:.3f})"
            f"*min(1,(it-{moment.start:.3f})/{ease:.3f})"
            f"*min(1,({moment.end:.3f}-it)/{ease:.3f})"
        )

    envelope = "+".join(terms)
    zoom_expr = f"1+{zoom - 1:.4f}*max(0,{envelope})"

    return (
        f"zoompan=z='{zoom_expr}'"
        ":x='iw/2-(iw/zoom/2)'"
        ":y='ih/2-(ih/zoom/2)'"
        f":d=1:s={width}x{height}:fps={effective_fps:g}"
    )
