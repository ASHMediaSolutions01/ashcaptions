"""The captioning pipeline itself -- the only place ``engine`` and
``languages`` meet (spec sections 8, 9, 10).

``build_run_job()`` returns the ``RunJob`` callable ``pipeline.JobWorker``
executes for each queued job: resolve the (language, dialect) choice,
extract audio, transcribe (and translate, if asked), post-process the
text, build caption cards, write every output file, optionally burn in,
then -- the single most dangerous line in this module -- delete the input
file, but *only* if it came from the watch folder. A file submitted by
path lives wherever the editor keeps their footage; deleting a client's
source file would be a catastrophe, so that check is not optional (spec
section 10, 12).
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import Callable

from ash_captions import engine, languages, styles
from ash_captions.config import Settings, find_binary
from ash_captions.pipeline.db import Job

from .catalogue import dialect_preset_id

RunJob = Callable[[Job, Callable[[int], None]], None]

# engine.rules.build_cards()'s own default for its `min_words` floor --
# not re-exported from engine, so mirrored here as the ceiling
# `style.layout.max_words` is clamped against (see run_job): a style
# asking for 1- or 2-word cards (e.g. HYPE) must not have that request
# undone by a `min_words` floor higher than the style's own max.
_DEFAULT_MIN_WORDS_PER_CARD = 3

# Base weights for the progress bar. Transcription dominates real runtime
# (spec section 9: "timing quality is the feature"), so it must dominate
# the bar too -- not a naive linear 20/40/60/80 split. Stages that don't
# apply to a given job (translate, burn) are dropped and the rest
# re-normalised to still span 0-100 -- see ``_progress_budget``.
_STAGE_ORDER = ("extract", "transcribe", "translate", "postprocess", "cards_and_write", "burn")
_BASE_WEIGHTS = {
    "extract": 5,
    "transcribe": 55,
    "translate": 15,
    "postprocess": 3,
    "cards_and_write": 7,
    "burn": 15,
}


def _progress_budget(*, translate: bool, burn: bool) -> dict[str, tuple[int, int]]:
    """Allocate ``_BASE_WEIGHTS`` proportionally over the stages that
    actually run for this job, returning ``{stage: (start_pct, end_pct)}``.
    The last active stage always ends exactly at 100, regardless of
    rounding drift in the stages before it.
    """
    active = {"extract", "transcribe", "postprocess", "cards_and_write"}
    if translate:
        active.add("translate")
    if burn:
        active.add("burn")
    total_weight = sum(weight for name, weight in _BASE_WEIGHTS.items() if name in active)

    budget: dict[str, tuple[int, int]] = {}
    cursor = 0.0
    for name in _STAGE_ORDER:
        if name not in active:
            continue
        share = _BASE_WEIGHTS[name] / total_weight * 100
        start, cursor = cursor, cursor + share
        budget[name] = (round(start), round(cursor))

    last_stage = next(name for name in reversed(_STAGE_ORDER) if name in active)
    start, _ = budget[last_stage]
    budget[last_stage] = (start, 100)
    return budget


def _postprocess_words(
    words: tuple, resolved: languages.ResolvedDialect, glossary_path: Path
) -> tuple:
    """Apply the post-processing chain (dialect glossary, client glossary,
    spelling) word-by-word, preserving each word's timing.

    Matching per-word (rather than over the full segment) means a
    multi-word glossary phrase only reliably fires in the plain-text
    transcript (``_postprocess_segments``, below), not in the word-timed
    captions -- an accepted limitation given ``build_cards`` needs
    per-``Word`` timing to produce the .srt/.ass files, and most glossary
    corrections in practice are single terms (a name, a brand).
    """
    return tuple(
        dataclasses.replace(word, text=languages.postprocess(word.text, resolved, glossary_path))
        for word in words
    )


def _postprocess_segments(
    segments: tuple, resolved: languages.ResolvedDialect, glossary_path: Path
) -> tuple:
    return tuple(
        dataclasses.replace(seg, text=languages.postprocess(seg.text, resolved, glossary_path))
        for seg in segments
    )


def _is_within(path: Path, directory: Path) -> bool:
    """True if ``path`` resolves to somewhere inside ``directory``."""
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def build_run_job(
    settings: Settings,
    *,
    watch_dir: Path,
    transcriber: engine.Transcriber | None = None,
    ffmpeg_path: Path | None = None,
) -> RunJob:
    """Build the ``run_job`` callable ``JobWorker`` executes.

    ``transcriber`` and ``ffmpeg_path`` are injectable so tests never need
    a real faster-whisper model or ffmpeg binary. In production both
    default lazily: a single ``WhisperTranscriber`` is constructed on
    first use and reused for every subsequent job (model loading is the
    expensive part; the worker thread already serialises jobs one at a
    time, so sharing one instance across them is safe and avoids reloading
    per job), and ffmpeg is resolved via ``config.find_binary`` (bundled
    ``bin/ffmpeg.exe``, falling back to PATH).
    """
    resolved_ffmpeg = ffmpeg_path or find_binary("ffmpeg") or engine.DEFAULT_FFMPEG_PATH
    watch_dir = Path(watch_dir)
    lazy_transcriber: dict[str, engine.Transcriber] = {}

    def get_transcriber() -> engine.Transcriber:
        if transcriber is not None:
            return transcriber
        if "instance" not in lazy_transcriber:
            lazy_transcriber["instance"] = engine.WhisperTranscriber(
                settings.model_size,
                device=settings.device,
                download_root=settings.model_cache_dir,
            )
        return lazy_transcriber["instance"]

    def run_job(job: Job, report: Callable[[int], None]) -> None:
        video_path = Path(job.input_path)
        output_dir = Path(job.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = video_path.stem

        preset_id = dialect_preset_id(job.options.language, job.options.dialect)
        resolved = languages.resolve(job.options.language, preset_id)
        client_glossary_path = settings.glossary_dir / "glossary.txt"
        # Never raises -- an unknown or invalid style name falls back to
        # styles.DEFAULT_STYLE (spec 7A.4), so a bad/renamed style can
        # never fail a job.
        style = styles.resolve_style(job.options.preset)
        card_max_words = style.layout.max_words
        card_min_words = min(_DEFAULT_MIN_WORDS_PER_CARD, card_max_words)

        budget = _progress_budget(translate=job.options.translate, burn=job.options.burn)
        model = get_transcriber()

        with tempfile.TemporaryDirectory(prefix="ash-captions-") as tmp_dir:
            audio_path = Path(tmp_dir) / f"{stem}.wav"

            report(budget["extract"][0])
            engine.extract_audio(video_path, audio_path, ffmpeg_path=resolved_ffmpeg)
            report(budget["extract"][1])

            report(budget["transcribe"][0])
            result = model.transcribe(
                audio_path,
                language=resolved.whisper_language,
                initial_prompt=resolved.initial_prompt or None,
            )
            report(budget["transcribe"][1])

            translation = None
            if job.options.translate:
                report(budget["translate"][0])
                translation = model.translate(
                    audio_path,
                    language=resolved.whisper_language,
                    initial_prompt=resolved.initial_prompt or None,
                )
                report(budget["translate"][1])

            report(budget["postprocess"][0])
            words = _postprocess_words(result.words, resolved, client_glossary_path)
            segments = _postprocess_segments(result.segments, resolved, client_glossary_path)
            report(budget["postprocess"][1])

            report(budget["cards_and_write"][0])
            cards = engine.build_cards(words, max_words=card_max_words, min_words=card_min_words)
            engine.write_srt(cards, output_dir / f"{stem}.srt")
            engine.write_ass(cards, output_dir / f"{stem}.ass", style)
            engine.write_txt(segments, output_dir / f"{stem}.txt")
            if translation is not None:
                en_words = _postprocess_words(translation.words, resolved, client_glossary_path)
                en_cards = engine.build_cards(en_words, max_words=card_max_words, min_words=card_min_words)
                engine.write_srt(en_cards, output_dir / f"{stem}.en.srt")
            report(budget["cards_and_write"][1])

            if job.options.burn:
                start, end = budget["burn"]
                # Engine doesn't probe video duration itself (burn.py takes
                # it as a parameter); the transcript's own last timestamp is
                # a close enough proxy to drive the burn-in progress bar
                # without adding a separate ffprobe call here.
                duration = max((seg.end for seg in result.segments), default=0.0)

                def on_burn_progress(pct: float, start=start, end=end) -> None:
                    report(round(start + (end - start) * (pct / 100)))

                # NOT WIRED YET: engine.burn_captions()/build_burn_command()
                # take no fontsdir parameter, so a bundled (non-Windows-
                # installed) font silently falls back to a default face on
                # burn-in -- defeating the point of bundling fonts at all.
                # The value to pass, once engine adds support, is
                # `styles.fontsdir_arg()`. Flagged to the engine owner
                # rather than reached into from here.
                engine.burn_captions(
                    video_path,
                    output_dir / f"{stem}.ass",
                    output_dir / f"{stem}.captioned.mp4",
                    duration_seconds=duration,
                    ffmpeg_path=resolved_ffmpeg,
                    on_progress=on_burn_progress,
                )
                report(end)

        # DANGER: only ever delete a file that came from the watch folder.
        # A path submitted directly by an editor points at their live
        # footage -- deleting it would destroy a client's source file.
        if _is_within(video_path, watch_dir):
            video_path.unlink(missing_ok=True)

    return run_job
