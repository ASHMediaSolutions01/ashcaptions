"""The saved transcript beside a job's outputs: ``<stem>.transcript.json``.

Why it exists (v0.3, the Studio page): once a video has been transcribed
once, every further caption operation -- re-styling into another look,
burning after the editor has picked one, retrying a failed burn -- is a
matter of seconds if the word timings are on disk, and a matter of
minutes (an hour, for a long file) if they have to be produced again.
The transcript is the expensive, stable part; captions are cheap views
of it.

The record carries the source file's size and mtime so a stale
transcript (the editor re-exported the video under the same name) is
never reused for the wrong footage.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ash_captions.engine import Segment, Word

TRANSCRIPT_SUFFIX = ".transcript.json"
FORMAT_VERSION = 1


@dataclass(frozen=True)
class SourceStamp:
    """Enough to notice the input file changed since transcription."""

    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> "SourceStamp":
        stat = os.stat(path)
        return cls(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


@dataclass(frozen=True)
class TranscriptRecord:
    language: str
    words: tuple[Word, ...]
    segments: tuple[Segment, ...]
    en_words: tuple[Word, ...] | None = None
    play_res: tuple[int, int] | None = None
    source: SourceStamp | None = None
    dialect: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def matches(self, video_path: Path) -> bool:
        """True when ``video_path`` is the file this transcript came from."""
        if self.source is None:
            return False
        try:
            return SourceStamp.of(video_path) == self.source
        except OSError:
            return False


def transcript_path(output_dir: Path, stem: str) -> Path:
    return Path(output_dir) / f"{stem}{TRANSCRIPT_SUFFIX}"


def _word_to_dict(w: Word) -> dict[str, Any]:
    return {"t": w.text, "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)}


def _word_from_dict(d: dict[str, Any]) -> Word:
    return Word(text=str(d["t"]), start=float(d["s"]), end=float(d["e"]), probability=float(d.get("p", 1.0)))


def save_transcript(path: Path, record: TranscriptRecord) -> Path:
    """Write atomically (temp + replace), like every other job output."""
    payload = {
        "format": FORMAT_VERSION,
        "language": record.language,
        "dialect": record.dialect,
        "play_res": list(record.play_res) if record.play_res else None,
        "source": {"size": record.source.size, "mtime_ns": record.source.mtime_ns} if record.source else None,
        "words": [_word_to_dict(w) for w in record.words],
        "segments": [
            {"text": s.text, "s": round(s.start, 3), "e": round(s.end, 3), "words": [_word_to_dict(w) for w in s.words]}
            for s in record.segments
        ],
        "en_words": [_word_to_dict(w) for w in record.en_words] if record.en_words is not None else None,
        "extra": record.extra,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


class TranscriptError(Exception):
    """The transcript file is missing, unreadable, or from another format."""


def load_transcript(path: Path) -> TranscriptRecord:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TranscriptError(f"No saved transcript at {path}") from exc
    except (OSError, ValueError) as exc:
        raise TranscriptError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT_VERSION:
        raise TranscriptError(f"{path} is not a transcript this version understands")
    try:
        words = tuple(_word_from_dict(w) for w in data["words"])
        segments = tuple(
            Segment(
                text=str(s["text"]),
                start=float(s["s"]),
                end=float(s["e"]),
                words=tuple(_word_from_dict(w) for w in s.get("words", [])),
            )
            for s in data.get("segments", [])
        )
        en_raw = data.get("en_words")
        en_words = tuple(_word_from_dict(w) for w in en_raw) if en_raw is not None else None
        play_raw = data.get("play_res")
        play_res = (int(play_raw[0]), int(play_raw[1])) if play_raw else None
        src_raw = data.get("source")
        source = SourceStamp(size=int(src_raw["size"]), mtime_ns=int(src_raw["mtime_ns"])) if src_raw else None
    except (KeyError, TypeError, ValueError) as exc:
        raise TranscriptError(f"{path} is malformed: {exc}") from exc
    return TranscriptRecord(
        language=str(data.get("language", "")),
        dialect=data.get("dialect"),
        words=words,
        segments=segments,
        en_words=en_words,
        play_res=play_res,
        source=source,
        extra=dict(data.get("extra") or {}),
    )
