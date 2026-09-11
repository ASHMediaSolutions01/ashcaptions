"""Emoji bursts: the one caption effect ASS cannot draw.

Every other treatment in this tool is an override tag libass renders.
Colour emoji are not: ASS has no colour glyphs, so a burst has to be a
*compositing* pass over the burned frame -- an image laid on top for a
moment, the way a sticker is.

Measured before it was designed, on a real 1080x1920 reel:

* **A chain of timed `overlay` filters is the right shape.** 1, 10, 50,
  150 and 300 of them all build and run: 0.8s, 0.9s, 1.0s, 1.5s and 2.3s
  for twelve seconds of 1080x1920. The cost tracks how many are *on* at
  once rather than how many exist, because `overlay` with a false
  `enable` passes the frame straight through.
* **The alpha survives**, checked on the pixels rather than assumed --
  a disc with a transparent surround composited cleanly over footage
  that already had captions burned into it.

That 300 figure matters because the neighbouring feature already hit a
limit of exactly this kind: ffmpeg's *expression* parser gives up past
about 80 chained `+` terms, which is why ``punch.build_punch_filter``
sums in a balanced tree. A chain of overlay filters is a different
mechanism with a different limit, so it was measured rather than assumed
-- but it is not unlimited either, and ``MAX_BURSTS`` keeps a long
documentary from finding out where it ends.

Which emoji fires is the same rule sounds already use: the look's list is
cycled in order, so two emoji alternate and one repeats. Predictable
beats clever for the reason ``select_punch_moments`` gives -- an editor
should watch one video and know what the next fifty will do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .sfx import MIN_SPACING_FLOOR, _normalise, is_sentence_start
from .transcribe import Word

# Measured ceiling: 300 timed overlays composite in 2.3s for a 12s
# 1080x1920 clip. Stopping well short of that keeps an hour-long file
# from discovering where the real limit is, and a reel with more than a
# hundred emoji in it is not a reel anybody wants.
MAX_BURSTS = 120

# How long one sticker stays. Short enough to read as a punctuation mark
# on the sentence rather than as a layer someone forgot to remove.
BURST_SECONDS = 0.8

# How far it drifts up over its life, as a share of the frame height: a
# burst that simply appears reads as a rendering fault, and a rise is the
# cheapest motion that reads as deliberate -- one expression on the
# overlay's own y, rather than a filter per sticker.
RISE_FRACTION = 0.04

# Sticker size as a share of the frame's short side. 0.12 was the first
# guess and it was too small: burned onto the real reel the emoji came out
# about the height of one caption letter, which reads as a glyph in the
# text rather than as a burst over it. 0.20 is about three caption letters
# tall, which is what a sticker looks like.
SIZE_FRACTION = 0.20

MIN_SIZE_PX = 48


class StickerError(Exception):
    """A burst could not be prepared."""


@dataclass(frozen=True, slots=True)
class Burst:
    """One emoji, at one moment, on one side of the caption."""

    time: float
    emoji: str
    side: int  # -1 left of centre, +1 right
    trigger: str  # "sentence" or "keyword" -- for logging and tests


@dataclass(frozen=True, slots=True)
class StickerPlan:
    """What the burn needs: the files to open, and the graph to chain.

    Mirrors ``sfx.SfxPlan`` deliberately, including why it exists:
    ``engine/burn.py`` must never import the style package, and the burn
    owns input numbering because that changes when a matte is in play.
    """

    files: tuple[str, ...] = ()
    bursts: tuple[Burst, ...] = ()
    emoji_order: tuple[str, ...] = ()
    width: int = 1080
    height: int = 1920

    @property
    def size_px(self) -> int:
        return max(MIN_SIZE_PX, int(min(self.width, self.height) * SIZE_FRACTION))

    def filtergraph(
        self, *, base_input_index: int, in_label: str, out_label: str = "stuck"
    ) -> str | None:
        """Chain one timed ``overlay`` per burst onto ``in_label``.

        Each sticker is scaled once -- a `scale` per input, not per burst
        -- and then **split** into one output per time it fires.

        The split is not an optimisation, it is the whole correctness of
        this. An ffmpeg filter output pad feeds exactly one input, so a
        graph that names ``[e0]`` twice silently gives it to the first
        consumer and the second overlay draws nothing at all -- no error,
        no warning, exit code 0. Burning the real reel is what found it:
        an emoji used twice appeared the first time and was simply
        missing the second, while the graph read perfectly.
        """
        if not self.bursts or not self.files:
            return None

        size = self.size_px
        index_of = {name: i for i, name in enumerate(self.emoji_order)}
        usable = [b for b in self.bursts[:MAX_BURSTS] if index_of.get(b.emoji) is not None]
        if not usable:
            return None

        # How many times each emoji fires decides how many copies of its
        # stream the graph has to make.
        uses: dict[int, int] = {}
        for burst in usable:
            source = index_of[burst.emoji]
            uses[source] = uses.get(source, 0) + 1

        steps = []
        for i in range(len(self.files)):
            count = uses.get(i, 0)
            if count == 0:
                continue
            outs = "".join("[e%d_%d]" % (i, n) for n in range(count))
            split = ",split=%d" % count if count > 1 else ""
            steps.append(
                "[%d:v]scale=%d:%d%s%s"
                % (base_input_index + i, size, size, split, outs)
            )

        taken: dict[int, int] = {}
        label = in_label
        drawn = 0
        for burst in usable:
            source = index_of[burst.emoji]
            copy = taken.get(source, 0)
            taken[source] = copy + 1
            end = burst.time + BURST_SECONDS
            # Centred a third of the way in from the edge, above the
            # caption band, rising as it goes.
            x = int(self.width * (0.5 + burst.side * 0.28)) - size // 2
            top = int(self.height * 0.62)
            rise = int(self.height * RISE_FRACTION)
            y = "%d-%d*min(1,(t-%.3f)/%.3f)" % (top, rise, burst.time, BURST_SECONDS)
            nxt = "[s%d]" % drawn
            steps.append(
                "%s[e%d_%d]overlay=%d:'%s':enable='between(t,%.3f,%.3f)'%s"
                % (label, source, copy, x, y, burst.time, end, nxt)
            )
            label = nxt
            drawn += 1
        if not drawn:
            return None
        # Rename the last link to what the caller asked for.
        steps[-1] = steps[-1][: -len(label)] + "[%s]" % out_label
        return ";".join(steps)


def select_bursts(
    words: Sequence[Word],
    *,
    trigger: str = "keyword",
    emoji: Sequence[str] = (),
    keywords: Sequence[str] = (),
    min_spacing: float = 2.0,
    sentence_gap: float = 0.6,
    video_duration: float | None = None,
    limit: int = MAX_BURSTS,
) -> list[Burst]:
    """When a sticker fires, and which one.

    ``min_spacing`` defaults far wider than the sound effects' 0.35s: a
    noise every third of a second is a rhythm, and a picture every third
    of a second is a mess.
    """
    usable = [name for name in emoji if name]
    if trigger == "off" or not words or not usable:
        return []

    spacing = max(MIN_SPACING_FLOOR, float(min_spacing))
    wanted = {_normalise(k) for k in keywords if _normalise(k)}
    word_list = list(words)

    bursts: list[Burst] = []
    last: float | None = None
    for index, word in enumerate(word_list):
        kind: str | None = None
        if trigger in ("sentence", "both") and is_sentence_start(
            index, word_list, sentence_gap
        ):
            kind = "sentence"
        if kind is None and trigger in ("keyword", "both") and _normalise(word.text) in wanted:
            kind = "keyword"
        if kind is None:
            continue
        if last is not None and (word.start - last) < spacing:
            continue
        if video_duration is not None and word.start >= video_duration:
            break
        last = word.start
        bursts.append(
            Burst(
                time=float(word.start),
                emoji=usable[len(bursts) % len(usable)],
                # Alternating sides, so two bursts close together never
                # land on top of one another.
                side=-1 if len(bursts) % 2 == 0 else 1,
                trigger=kind,
            )
        )
        if len(bursts) >= limit:
            break
    return bursts


def build_plan(
    bursts: Sequence[Burst],
    resolve: Callable[[str], object | None],
    *,
    width: int,
    height: int,
) -> StickerPlan | None:
    """Turn chosen bursts into a plan, or None if none of them survive.

    ``resolve`` maps an emoji name to a file on disk (or None). A name
    that resolves to nothing is dropped rather than failing the burn --
    the same rule sounds follow, and for the same reason: a look
    referring to an emoji this build does not ship should cost the editor
    a missing sticker, not a missing video.
    """
    if not bursts:
        return None
    order: list[str] = []
    files: list[str] = []
    for name in dict.fromkeys(b.emoji for b in bursts):
        path = resolve(name)
        if path is None:
            continue
        order.append(name)
        files.append(str(path))
    if not files:
        return None
    keep = tuple(b for b in bursts if b.emoji in set(order))
    if not keep:
        return None
    return StickerPlan(
        files=tuple(files),
        bursts=keep,
        emoji_order=tuple(order),
        width=int(width),
        height=int(height),
    )
