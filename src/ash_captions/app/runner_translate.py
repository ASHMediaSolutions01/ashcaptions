"""The translate-only job (v0.5 caption check): add English to a saved
transcript without transcribing the source again.

An editor who does not speak the video's language wants to check the
captions against an English line. When the original job was run without
"translate to English", this mode reuses the saved words (the expensive,
stable part) and runs only the engine's English pass on the audio, then
post-processes, writes ``en_words`` into the transcript and
``<stem>.en.srt`` beside it. It never calls ``transcribe``; a job whose
transcript is missing (or is for a different version of the file) fails
with a plain message rather than silently transcribing.

Kept out of ``runner.py`` for size; ``build_run_job`` dispatches here when
``job.options.mode == "translate_only"`` and hands over the pieces it has
already built (the resolved dialect, glossary entries, the shared
transcriber getter and its own ``_run_transcriber`` progress wrapper).
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from ash_captions import engine, languages
from ash_captions.config import Settings
from ash_captions.pipeline.db import Job
from ash_captions.pipeline.queue import JobCancelled

from .runner_transcript import _reusable_transcript
from .runner_util import _CANCEL_EXCEPTIONS, _postprocess_words, _progress_budget, atomic_write
from .transcript import save_transcript, transcript_path

log = logging.getLogger(__name__)

NO_TRANSCRIPT_MESSAGE = (
    "No saved transcript for this video (or the file changed since it was "
    "transcribed). Run a normal captioning job first, then translate from the Studio."
)


def run_translate_only(
    job: Job,
    report: Callable[[int], None],
    *,
    settings: Settings,
    resolved: languages.ResolvedDialect,
    glossary_path: Path,
    glossary_entries: Any,
    get_transcriber: Callable[[], "engine.Transcriber"],
    ffmpeg_path: Path,
    run_transcriber: Callable[..., Any],
    card_words: tuple[int, int],
) -> None:
    """Run the English pass for ``job`` from its saved transcript.

    ``run_transcriber`` is ``build_run_job``'s own wrapper
    (``fn, audio_path, resolved, span, report, should_stop, initial_prompt``)
    so progress and cancellation behave exactly as in a full job.
    ``card_words`` is ``(max_words, min_words)`` for the English cards.
    Raises ``RuntimeError`` (``NO_TRANSCRIPT_MESSAGE``) without a usable
    transcript and ``JobCancelled`` when the engine was asked to stop.
    """
    set_stage: Callable[[str], None] = getattr(report, "stage", None) or (lambda _name: None)
    should_stop: Callable[[], bool] | None = getattr(report, "should_stop", None)
    video_path = Path(job.input_path)
    output_dir = Path(job.output_dir)
    stem = video_path.stem

    saved = _reusable_transcript(output_dir, stem, video_path, needs_translation=False)
    if saved is None:
        raise RuntimeError(NO_TRANSCRIPT_MESSAGE)
    model = get_transcriber()
    budget = _progress_budget(translate=True, burn=False, transcribe=False)
    max_words, min_words = card_words

    job_tmp = Path(settings.tmp_dir) / f"job-{job.id}"
    try:
        job_tmp.mkdir(parents=True, exist_ok=True)
        audio_path = job_tmp / f"{stem}.wav"

        set_stage("extract")
        report(budget["extract"][0])
        engine.extract_audio(video_path, audio_path, ffmpeg_path=ffmpeg_path)
        report(budget["extract"][1])

        set_stage("translate")
        # No dialect priming on the English pass (see runner.py: the source
        # prompt left chunks of the "English" output in Spanish).
        translation = run_transcriber(
            model.translate, audio_path, resolved, budget["translate"], report, should_stop, initial_prompt=None
        )

        set_stage("postprocess")
        report(budget["postprocess"][0])
        en_words = tuple(
            _postprocess_words(translation.words, languages.resolve("en"), glossary_path, entries=glossary_entries)
        )
        report(budget["postprocess"][1])
        # Not best-effort like a full job's save: the English words *are*
        # this job's deliverable, so failing to save them fails the job.
        save_transcript(transcript_path(output_dir, stem), dataclasses.replace(saved, en_words=en_words))

        set_stage("write")
        report(budget["cards_and_write"][0])
        en_cards = engine.build_cards(
            en_words, max_words=max_words, min_words=min_words, silence_gap=settings.silence_gap_seconds
        )
        atomic_write(lambda p: engine.write_srt(en_cards, p), output_dir / f"{stem}.en.srt")
        report(100)
        log.info("job %s: added %d English words to the saved transcript", job.id, len(en_words))
    except _CANCEL_EXCEPTIONS as exc:
        raise JobCancelled(str(exc)) from exc
    finally:
        shutil.rmtree(job_tmp, ignore_errors=True)
