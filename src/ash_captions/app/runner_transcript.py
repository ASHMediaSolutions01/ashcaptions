"""Saved-transcript reuse for the job runner (split from runner.py for size)."""

from __future__ import annotations

import logging
from pathlib import Path

from ash_captions import engine, languages
from ash_captions.pipeline.db import Job

from .transcript import SourceStamp, TranscriptError, TranscriptRecord, load_transcript, save_transcript, transcript_path

log = logging.getLogger(__name__)


def _reusable_transcript(
    output_dir: Path, stem: str, video_path: Path, *, needs_translation: bool
) -> "TranscriptRecord | None":
    """The saved transcript for this exact file, or None when there is none,
    it is for a different version of the file, or it lacks the English
    words a translate job needs."""
    path = transcript_path(output_dir, stem)
    if not path.is_file():
        return None
    try:
        record = load_transcript(path)
    except TranscriptError as exc:
        log.warning("ignoring saved transcript %s: %s", path, exc)
        return None
    if not record.matches(video_path):
        log.info("saved transcript %s is for a different version of the file; transcribing again", path)
        return None
    if needs_translation and record.en_words is None:
        return None
    return record


def _save_transcript(
    output_dir: Path,
    stem: str,
    video_path: Path,
    job: Job,
    resolved: languages.ResolvedDialect,
    words: tuple,
    segments: tuple,
    en_words: tuple | None,
    info: "engine.VideoInfo | None",
) -> None:
    """Best effort: a transcript that fails to save must not fail the job
    (the captions are already about to be written)."""
    try:
        record = TranscriptRecord(
            language=job.options.language,
            dialect=job.options.dialect,
            words=tuple(words),
            segments=tuple(segments),
            en_words=tuple(en_words) if en_words is not None else None,
            play_res=(info.width, info.height) if info is not None else None,
            source=SourceStamp.of(video_path),
        )
        save_transcript(transcript_path(output_dir, stem), record)
    except Exception:  # noqa: BLE001
        log.warning("could not save the transcript beside the outputs", exc_info=True)
