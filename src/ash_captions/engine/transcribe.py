"""Speech-to-text transcription behind a narrow, swappable interface.

Per spec 9.2, word-timing quality is the feature: it drives the POP
caption preset. v1 uses faster-whisper's own word timestamps (DTW over
attention weights). If real client work later shows visible timing
drift, the plan is to swap in WhisperX (wav2vec2 forced alignment)
*without touching callers*.

That swap only stays cheap if every caller depends on the ``Transcriber``
protocol and the ``Word`` / ``Segment`` / ``TranscriptionResult``
dataclasses below -- never on faster-whisper's own types. Keep it that
way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Word:
    """A single transcribed word with timing, in seconds from audio start."""

    text: str
    start: float
    end: float
    probability: float = 1.0


@dataclass(frozen=True, slots=True)
class Segment:
    """A contiguous run of speech, made up of words."""

    text: str
    start: float
    end: float
    words: tuple[Word, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """The full output of one transcribe/translate call."""

    segments: tuple[Segment, ...]
    language: str
    language_probability: float = 1.0

    @property
    def words(self) -> tuple[Word, ...]:
        return tuple(word for segment in self.segments for word in segment.words)


class TranscriptionError(Exception):
    """Raised when the transcription backend fails to load or run."""


@runtime_checkable
class Transcriber(Protocol):
    """Narrow interface every transcription backend must satisfy.

    Callers (caption rules, the web layer, batch jobs) depend only on this
    protocol and on ``Word`` / ``Segment`` / ``TranscriptionResult`` -- never
    on a specific backend -- so the backend can be swapped later (e.g. for
    WhisperX) without touching them.
    """

    def transcribe(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        ...

    def translate(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        ...


class WhisperTranscriber:
    """``Transcriber`` implementation backed by faster-whisper's WhisperModel.

    The faster-whisper import is deferred to first use so that importing
    this module -- and constructing a ``WhisperTranscriber`` -- never
    requires faster-whisper (or a model download) to be present. That keeps
    the rest of the engine testable without the real dependency installed.
    """

    def __init__(
        self,
        model_size: str = "small",
        *,
        device: str = "cpu",
        compute_type: str | None = None,
        download_root: Path | str | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type or ("float16" if device == "cuda" else "int8")
        self.download_root = download_root
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError("faster-whisper is not installed") from exc

        try:
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.download_root) if self.download_root else None,
            )
        except Exception as exc:  # faster-whisper raises plain Exception/RuntimeError
            raise TranscriptionError(
                f"Failed to load Whisper model '{self.model_size}' on {self.device}: {exc}"
            ) from exc
        return self._model

    def _run(
        self,
        audio_path: Path | str,
        *,
        task: str,
        language: str | None,
        initial_prompt: str | None,
        vad_filter: bool,
    ) -> TranscriptionResult:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        model = self._load()
        try:
            raw_segments, info = model.transcribe(
                str(audio_path),
                task=task,
                language=language,
                initial_prompt=initial_prompt,
                word_timestamps=True,
                vad_filter=vad_filter,
            )
        except Exception as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

        segments = tuple(_convert_segment(seg) for seg in raw_segments)
        return TranscriptionResult(
            segments=segments,
            language=info.language,
            language_probability=getattr(info, "language_probability", 1.0),
        )

    def transcribe(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        return self._run(
            audio_path,
            task="transcribe",
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=vad_filter,
        )

    def translate(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        return self._run(
            audio_path,
            task="translate",
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=vad_filter,
        )


def _convert_segment(raw_segment) -> Segment:
    raw_words = raw_segment.words or []
    words = tuple(
        Word(
            text=w.word.strip(),
            start=w.start,
            end=w.end,
            probability=getattr(w, "probability", 1.0),
        )
        for w in raw_words
    )
    return Segment(
        text=raw_segment.text.strip(),
        start=raw_segment.start,
        end=raw_segment.end,
        words=words,
    )
