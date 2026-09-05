"""Sound effects locked to the caption word (v0.7 section 1).

A whoosh that lands exactly when the caption arrives is the difference
between a captioned video and an edited one, in the same way a punch-in
is -- and we already know, to the millisecond, when every word is said.
So this module is punch-in's twin: choose the moments from the words,
build the ffmpeg fragment, and hand both to the burn.

Two decisions are worth stating, because both could reasonably have gone
the other way:

* **The moments are chosen by rule, not by analysing the audio.** A
  competitor advertises "impact analysed"; energy detection produces
  "why did it whoosh there" moments, and an editor cannot predict the
  fiftieth video from watching the first. Sentence starts and keywords
  are boring and trustworthy, which is what a house style needs.
* **The mix happens inside the burn, not in a separate pass.** The burn
  already re-muxes the audio; adding inputs to that one ffmpeg call
  costs nothing, and a second pass would mean a second generation of
  lossy encoding on the client's dialogue.

Scale: the graph is one ``adelay`` per hit plus a tree of ``amix`` nodes,
read from a file like the punch envelope, so it is bounded by
``MAX_HITS`` rather than by the Windows command-line limit. There is no
expression-recursion problem here of the kind ``punch._balanced_sum``
exists to avoid -- these are separate filter instances -- but the mix is
still built as a tree, because one ``amix`` with a thousand inputs
allocates a thousand input buffers up front.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from .punch import SENTENCE_END, is_sentence_start  # noqa: F401  (SENTENCE_END re-exported)
from .transcribe import Word

# Past this many hits a video is not being decorated, it is being
# machine-gunned; the cap also keeps the filtergraph and ffmpeg's buffer
# allocation bounded on a feature-length file. Hits beyond it are
# dropped, and the caller logs that they were.
MAX_HITS = 1000

# One ``amix`` node takes at most this many inputs; deeper mixes are a
# tree of them.
MIX_FANIN = 32

# A hit closer than this to the one before is inaudible as a separate
# event and just makes the mix muddy.
MIN_SPACING_FLOOR = 0.05

MIN_GAIN_DB, MAX_GAIN_DB = -40.0, 6.0
MIN_OFFSET_MS, MAX_OFFSET_MS = -500, 500

# The sample rate and layout every branch is conformed to before the mix.
# ffmpeg would negotiate this itself; pinning it means the graph renders
# the same whatever the source audio happens to be.
MIX_SAMPLE_RATE = 48_000
MIX_LAYOUT = "stereo"


class SfxTrigger(str, Enum):
    """When a sound fires. Mirrors ``PunchMode`` and adds ``WORD``."""

    OFF = "off"
    SENTENCE = "sentence"   # the first word of each sentence
    KEYWORD = "keyword"     # words on the client's list
    BOTH = "both"           # sentence starts and keywords
    WORD = "word"           # every word -- for the reel looks, with a click


@dataclass(frozen=True, slots=True)
class SfxHit:
    """One sound, at one moment, in seconds from the start of the video."""

    time: float
    sound: str
    trigger: str  # "sentence", "keyword" or "word" -- for logging and tests


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def select_sfx_hits(
    words: Sequence[Word],
    *,
    trigger: SfxTrigger | str = SfxTrigger.SENTENCE,
    sounds: Sequence[str] = (),
    keywords: Sequence[str] = (),
    min_spacing: float = 0.35,
    offset_ms: int = 0,
    sentence_gap: float = 0.6,
    video_duration: float | None = None,
) -> list[SfxHit]:
    """Choose when a sound fires, and which one.

    Which sound: the style's list is cycled in order, so a two-sound look
    alternates and a one-sound look repeats. Predictable beats clever
    here for the same reason it does in ``select_punch_moments`` -- an
    editor should be able to watch one video and know what the next fifty
    will do.

    ``offset_ms`` shifts every hit, positive for later. It is the
    frame-precise nudge: a whoosh usually wants to start slightly
    *before* the word it belongs to, because the ear places a sound by
    its attack and the eye places a caption by the moment it is fully
    drawn.
    """
    trigger = SfxTrigger(trigger)
    usable = [name for name in sounds if name]
    if trigger is SfxTrigger.OFF or not words or not usable:
        return []

    min_spacing = max(MIN_SPACING_FLOOR, min_spacing)
    offset = max(MIN_OFFSET_MS, min(MAX_OFFSET_MS, int(offset_ms))) / 1000.0
    wanted = {_normalise(k) for k in keywords if _normalise(k)}
    word_list = list(words)

    hits: list[SfxHit] = []
    last_time: float | None = None

    for index, word in enumerate(word_list):
        kind: str | None = None
        if trigger is SfxTrigger.WORD:
            kind = "word"
        else:
            if trigger in (SfxTrigger.SENTENCE, SfxTrigger.BOTH) and is_sentence_start(
                index, word_list, sentence_gap
            ):
                kind = "sentence"
            if (
                kind is None
                and trigger in (SfxTrigger.KEYWORD, SfxTrigger.BOTH)
                and _normalise(word.text) in wanted
            ):
                kind = "keyword"
        if kind is None:
            continue

        # Spacing is judged on the word's own time, not on the offset one:
        # nudging the whole track must not change *which* words fire.
        if last_time is not None and (word.start - last_time) < min_spacing:
            continue
        last_time = word.start

        at = word.start + offset
        if at < 0:
            at = 0.0
        if video_duration is not None and at >= video_duration:
            continue

        hits.append(SfxHit(time=at, sound=usable[len(hits) % len(usable)], trigger=kind))
        if len(hits) >= MAX_HITS:
            break

    return hits


# ---------------------------------------------------------------------------
# the filtergraph
# ---------------------------------------------------------------------------


def _mix_tree(labels: list[str], out_label: str, *, counter: list[int]) -> list[str]:
    """Chain ``amix`` nodes until ``labels`` becomes one stream named
    ``out_label``. ``duration=longest`` throughout: a sound near the end
    must not be clipped by an earlier-finishing sibling. The *final* mix
    against the dialogue is what decides the track's length, and that one
    is built by the caller."""
    if len(labels) == 1:
        return [f"[{labels[0]}]anull[{out_label}]"]

    chains: list[str] = []
    current = labels
    while len(current) > MIX_FANIN:
        nxt: list[str] = []
        for start in range(0, len(current), MIX_FANIN):
            group = current[start:start + MIX_FANIN]
            if len(group) == 1:
                nxt.append(group[0])
                continue
            counter[0] += 1
            label = f"m{counter[0]}"
            joined = "".join(f"[{name}]" for name in group)
            chains.append(f"{joined}amix=inputs={len(group)}:duration=longest:normalize=0[{label}]")
            nxt.append(label)
        current = nxt

    joined = "".join(f"[{name}]" for name in current)
    chains.append(f"{joined}amix=inputs={len(current)}:duration=longest:normalize=0[{out_label}]")
    return chains


def build_sfx_filter(
    hits: Sequence[SfxHit],
    *,
    sound_inputs: Mapping[str, int],
    gain_db: float = -8.0,
    source_audio: bool = True,
    duration_seconds: float | None = None,
    out_label: str = "aout",
) -> str | None:
    """The ``-filter_complex`` fragment that lays the hits under the
    dialogue, or ``None`` when there is nothing to lay.

    ``sound_inputs`` maps a sound's name to its ffmpeg input index -- the
    caller owns input numbering, because the burn's inputs depend on
    whether a matte is in play.

    ``source_audio=False`` is a video with no audio track (a screen
    recording, an export from a stills sequence). The hits then play over
    a generated silence of ``duration_seconds``, so the deliverable still
    has one audio stream rather than none.

    Returning ``None`` rather than a pass-through graph matters for the
    same reason it does in ``build_punch_filter``: a video with no hits
    should not pay for an audio filter pass, and -- more importantly --
    should keep ``-c:a copy`` and its untouched original dialogue.
    """
    usable = [hit for hit in hits if hit.sound in sound_inputs]
    if not usable:
        return None
    if not source_audio and (duration_seconds is None or duration_seconds <= 0):
        return None

    gain = max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(gain_db)))
    conform = f"aformat=sample_rates={MIX_SAMPLE_RATE}:channel_layouts={MIX_LAYOUT}"

    chains: list[str] = []

    # One asplit per sound that fires more than once: an ffmpeg input can
    # feed exactly one filter input, so N hits on one .wav need N copies.
    counts = Counter(hit.sound for hit in usable)
    branches: dict[str, list[str]] = {}
    for sound, count in counts.items():
        index = sound_inputs[sound]
        if count == 1:
            branches[sound] = [f"{index}:a"]
            continue
        labels = [f"c{index}_{n}" for n in range(count)]
        joined = "".join(f"[{name}]" for name in labels)
        chains.append(f"[{index}:a]asplit={count}{joined}")
        branches[sound] = labels

    hit_labels: list[str] = []
    for position, hit in enumerate(usable):
        source = branches[hit.sound].pop()
        label = f"h{position}"
        delay_ms = max(0, round(hit.time * 1000))
        chains.append(
            f"[{source}]{conform},adelay={delay_ms}:all=1,volume={gain:.2f}dB[{label}]"
        )
        hit_labels.append(label)

    counter = [0]
    chains.extend(_mix_tree(hit_labels, "sfxbed", counter=counter))

    if source_audio:
        base = "0:a"
    else:
        base = "silence"
        chains.append(
            f"anullsrc=r={MIX_SAMPLE_RATE}:cl={MIX_LAYOUT}"
            f",atrim=end={duration_seconds:.3f}[{base}]"
        )
    # ``duration=first`` against the dialogue: the burn's audio track ends
    # when the dialogue does, so a riser fired on the last word cannot
    # stretch the deliverable past its video.
    chains.append(
        f"[{base}][sfxbed]amix=inputs=2:duration=first:normalize=0[{out_label}]"
    )
    return ";".join(chains)


# ---------------------------------------------------------------------------
# what the burn is handed
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SfxPlan:
    """Everything the burn needs: which files to open, and the graph.

    This exists so ``engine/burn.py`` never imports the style package. The
    burn owns input numbering (it changes when a matte is in play) and
    nothing else about sound; the caller owns turning a look's sound names
    into files on disk.
    """

    hits: tuple[SfxHit, ...]
    sounds: tuple[tuple[str, str], ...]  # (name, absolute .wav path), in input order
    gain_db: float = -8.0

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(path for _, path in self.sounds)

    def filtergraph(
        self,
        *,
        base_input_index: int,
        source_audio: bool = True,
        duration_seconds: float | None = None,
        out_label: str = "aout",
    ) -> str | None:
        """``base_input_index`` is the ffmpeg input index of this plan's
        first ``.wav`` -- 1 for a plain burn, 2 when a matte took input 1."""
        return build_sfx_filter(
            self.hits,
            sound_inputs={name: base_input_index + n for n, (name, _) in enumerate(self.sounds)},
            gain_db=self.gain_db,
            source_audio=source_audio,
            duration_seconds=duration_seconds,
            out_label=out_label,
        )


def build_plan(
    hits: Sequence[SfxHit],
    resolve: "Callable[[str], object | None]",
    *,
    gain_db: float = -8.0,
) -> SfxPlan | None:
    """Turn chosen hits into a plan, or ``None`` if none of them survive.

    ``resolve`` maps a sound name to a path (or ``None``). A name that
    does not resolve loses its hits rather than failing the burn: a look
    referring to a sound this bundle does not carry must still produce
    the client's video.
    """
    paths: dict[str, str] = {}
    for name in dict.fromkeys(hit.sound for hit in hits):
        found = resolve(name)
        if found is not None:
            paths[name] = str(found)
    kept = tuple(hit for hit in hits if hit.sound in paths)
    if not kept:
        return None
    return SfxPlan(
        hits=kept,
        sounds=tuple((name, paths[name]) for name in paths),
        gain_db=gain_db,
    )
