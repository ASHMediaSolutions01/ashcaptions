"""Naming who is speaking, for the .srt a podcast editor hands over.

Kept out of ``runner.py`` because that module sits at the 500-line
ceiling the tests enforce, and because the whole stage is one question:
given this job and these cards, what name goes in front of each?

Two rules that are about the job rather than about audio:

* **It degrades, it does not fail.** Speaker names are an annotation on
  a transcript that is correct without them. A model that will not
  download, a video with no audio, or one voice where two were hoped for
  all end the same way -- no names, and the captions are unaffected.
  That is the opposite of the reel, which fails loudly, because there a
  failure hands back the wrong deliverable rather than a plainer one.

* **One voice means no names.** Clustering told to find two speakers
  always finds two, so a monologue comes back split down the middle.
  ``engine.diarise`` refuses that below a measured separation, and this
  passes the refusal straight through: a caption that announces a
  speaker change in the middle of one person talking is worse than a
  caption with no names in it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Sequence

from ash_captions import engine

log = logging.getLogger(__name__)


def wants_speaker_labels(job_options: Any) -> bool:
    return bool(getattr(job_options, "speaker_labels", False))


def card_speakers(
    job_options: Any,
    video_path: Path,
    cards: Sequence[Any],
    *,
    models_dir: Path | str,
    ffmpeg_path: Path | str,
    should_stop: Callable[[], bool] | None = None,
) -> list[str | None] | None:
    """A name per card, or None when there are no names to give.

    None rather than a list of Nones so the writer can tell "not asked
    for" from "asked for and there was one voice" without inspecting
    every entry.
    """
    if not wants_speaker_labels(job_options) or not cards:
        return None
    try:
        result = engine.diarise_media(
            video_path,
            models_dir=models_dir,
            ffmpeg_path=ffmpeg_path,
            should_stop=should_stop,
        )
    except engine.DiarisationError as exc:
        log.warning("speaker labels unavailable; writing the transcript without them: %s", exc)
        return None
    if not result.turns:
        log.info("speaker labels: one voice only, so none are written")
        return None

    totals = result.seconds_by_speaker()
    log.info(
        "speaker labels: %d speaker(s), %s",
        len(totals),
        ", ".join(
            "%s %.0fs" % (engine.speaker_name(k), v) for k, v in sorted(totals.items())
        ),
    )
    names: list[str | None] = []
    for card in cards:
        middle = (float(card.start) + float(card.end)) / 2.0
        who = result.speaker_at(middle)
        names.append(None if who is None else engine.speaker_name(who))
    return hold_until_a_sentence_ends(_carry_across_gaps(names), cards)


# A speaker change is only believed where a sentence has finished. These
# are the marks that end one; a comma does not.
SENTENCE_END = (".", "!", "?", "…", "?", "!", "。")


def hold_until_a_sentence_ends(
    names: list[str | None], cards: Sequence[Any]
) -> list[str | None]:
    """Do not let the speaker change in the middle of a sentence.

    The voice model is clustering right -- the two speakers separated at
    0.557 on the reference interview -- but the VAD's span boundaries are
    breaths, not turns, so a turn can start part-way through somebody's
    sentence. Measured, that produced exactly this in the .srt:

        Speaker 1: Son una herramienta para
        Speaker 2: autores que deciden liberar

    which is one sentence with two people's names on it. A real turn
    almost never begins mid-clause, so a change is held back until the
    previous card has actually finished a sentence. It costs a little
    accuracy at the boundary and removes a mistake a reader cannot help
    but notice.
    """
    if not names:
        return names
    out: list[str | None] = [names[0]]
    for index in range(1, len(names)):
        current, previous = names[index], out[-1]
        if current is None or current == previous or previous is None:
            out.append(current)
            continue
        finished = str(getattr(cards[index - 1], "text", "")).rstrip()
        out.append(current if finished.endswith(SENTENCE_END) else previous)
    return out


def _carry_across_gaps(names: list[str | None]) -> list[str | None]:
    """A card landing in a pause keeps the previous speaker's name.

    The VAD trims silence, so a card can sit just outside every turn --
    usually the tail of a sentence. Leaving it unnamed makes the next
    card re-announce a speaker who never stopped talking.
    """
    out: list[str | None] = []
    previous: str | None = None
    for name in names:
        if name is None:
            out.append(previous)
        else:
            out.append(name)
            previous = name
    return out
