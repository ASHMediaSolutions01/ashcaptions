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

Long recordings (the studio's 60-90 minute talks) are the reason for two
choices here:

* ``BatchedInferencePipeline`` rather than ``WhisperModel.transcribe``.
  The eager path computes one log-mel spectrogram for the *whole* file
  before decoding anything -- measured at 5 GB peak for 90 minutes. The
  batched pipeline extracts features per VAD chunk, so memory is bounded
  by the batch, not the file. Word timestamps come from the same DTW
  alignment either way.
* Segments are consumed one at a time, reporting progress and checking
  for cancellation as they arrive, instead of being materialised in one
  go at the end of an hour of silence.
"""
from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Devices whose load failure (missing cuBLAS/cuDNN DLLs, driver mismatch)
# is retried on the CPU. "auto" resolves to cuda when a GPU is present.
GPU_DEVICES = frozenset({"cuda", "auto"})


def default_compute_type(device: str) -> str:
    return "float16" if device == "cuda" else "int8"

ProgressCallback = Callable[[float, float], None]  # (seconds_done, total_seconds)
StopCheck = Callable[[], bool]

# ctranslate2 spreads work across this many threads. More than the
# physical core count only adds contention, and a 32-thread workstation
# still wants some CPU left for the editor working alongside a 90-minute
# transcription.
MAX_DEFAULT_CPU_THREADS = 16


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


class TranscriptionCancelled(TranscriptionError):
    """Raised when ``should_stop`` asked for the transcription to end early."""


@runtime_checkable
class Transcriber(Protocol):
    """Narrow interface every transcription backend must satisfy.

    Callers (caption rules, the web layer, batch jobs) depend only on this
    protocol and on ``Word`` / ``Segment`` / ``TranscriptionResult`` -- never
    on a specific backend -- so the backend can be swapped later (e.g. for
    WhisperX) without touching them.

    ``on_progress`` is called as each segment arrives with
    ``(seconds_done, total_seconds)``; ``should_stop`` is polled at the
    same cadence and a True answer raises ``TranscriptionCancelled``.
    Both are optional so existing callers and test fakes keep working.
    """

    def transcribe(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCheck | None = None,
    ) -> TranscriptionResult:
        ...

    def translate(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCheck | None = None,
    ) -> TranscriptionResult:
        ...


def default_cpu_threads() -> int:
    """Threads for ctranslate2 when the caller expresses no preference."""
    return max(1, min(os.cpu_count() or 4, MAX_DEFAULT_CPU_THREADS))


class WhisperTranscriber:
    """``Transcriber`` implementation backed by faster-whisper's WhisperModel.

    The faster-whisper import is deferred to first use so that importing
    this module -- and constructing a ``WhisperTranscriber`` -- never
    requires faster-whisper (or a model download) to be present. That keeps
    the rest of the engine testable without the real dependency installed.

    Args:
        cpu_threads: threads for ctranslate2; ``None`` picks
            ``default_cpu_threads()``. Previously never set, which left
            ctranslate2 at its own default.
        condition_on_previous_text: feed the previous window's text as a
            prompt for the next. Off by default: on long audio one
            hallucinated window poisons every window after it.
        hallucination_silence_threshold: seconds of silence past which a
            segment that Whisper places inside it is treated as a
            hallucination and skipped. Only the eager path honours it
            (the batched pipeline in faster-whisper 1.2 accepts but
            ignores it); it is passed either way.
        batch_size: VAD chunks decoded per batch by the batched pipeline.
        use_batched_pipeline: use ``BatchedInferencePipeline`` (bounded
            memory) rather than ``WhisperModel.transcribe``. The eager
            path is still used when a call asks for ``vad_filter=False``,
            which the batched pipeline cannot run on audio over 30s.
        local_files_only: never contact the Hugging Face Hub; load the
            model from ``download_root`` as it is. Otherwise every launch
            asks the Hub for a newer revision and may re-download it.

    After a successful load ``effective_device`` and
    ``effective_compute_type`` say where the model actually runs: a
    ``cuda``/``auto`` load that fails (missing cuBLAS/cuDNN DLLs, driver
    mismatch) is retried once on the CPU, and callers can surface that.
    """

    def __init__(
        self,
        model_size: str = "small",
        *,
        device: str = "cpu",
        compute_type: str | None = None,
        download_root: Path | str | None = None,
        cpu_threads: int | None = None,
        condition_on_previous_text: bool = False,
        hallucination_silence_threshold: float | None = 2.0,
        batch_size: int = 8,
        use_batched_pipeline: bool = True,
        local_files_only: bool = False,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type or default_compute_type(device)
        self.download_root = download_root
        self.cpu_threads = cpu_threads if cpu_threads is not None else default_cpu_threads()
        self.condition_on_previous_text = condition_on_previous_text
        self.hallucination_silence_threshold = hallucination_silence_threshold
        self.batch_size = max(1, batch_size)
        self.use_batched_pipeline = use_batched_pipeline
        self.local_files_only = local_files_only
        self.effective_device: str | None = None
        self.effective_compute_type: str | None = None
        self._model = None
        self._pipeline = None

    def _model_kwargs(self, device: str, compute_type: str) -> dict[str, Any]:
        return {
            "device": device,
            "compute_type": compute_type,
            "cpu_threads": self.cpu_threads,
            "download_root": str(self.download_root) if self.download_root else None,
            "local_files_only": self.local_files_only,
        }

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError("faster-whisper is not installed") from exc

        try:
            self._model = WhisperModel(self.model_size, **self._model_kwargs(self.device, self.compute_type))
            self.effective_device, self.effective_compute_type = self.device, self.compute_type
            return self._model
        except Exception as exc:  # faster-whisper raises plain Exception/RuntimeError
            if self.device not in GPU_DEVICES:
                raise TranscriptionError(
                    f"Failed to load Whisper model '{self.model_size}' on {self.device}: {exc}"
                ) from exc
            gpu_error = exc

        logger.warning(
            "Could not load Whisper model '%s' on %s (%s); retrying on the CPU",
            self.model_size, self.device, gpu_error,
        )
        cpu_compute_type = default_compute_type("cpu")
        try:
            self._model = WhisperModel(self.model_size, **self._model_kwargs("cpu", cpu_compute_type))
        except Exception as cpu_exc:
            raise TranscriptionError(
                f"Failed to load Whisper model '{self.model_size}' on {self.device} ({gpu_error}) "
                f"and on cpu ({cpu_exc})"
            ) from cpu_exc
        self.effective_device, self.effective_compute_type = "cpu", cpu_compute_type
        return self._model

    def _load_pipeline(self, model):
        """The batched pipeline wrapping ``model``, or ``None`` when the
        installed faster-whisper has none (older releases)."""
        if self._pipeline is not None:
            return self._pipeline
        try:
            from faster_whisper import BatchedInferencePipeline
        except ImportError:
            return None
        self._pipeline = BatchedInferencePipeline(model)
        return self._pipeline

    def _run(  # noqa: C901 - one straight path, documented inline
        self,
        audio_path: Path | str,
        *,
        task: str,
        language: str | None,
        initial_prompt: str | None,
        vad_filter: bool,
        on_progress: ProgressCallback | None,
        should_stop: StopCheck | None,
    ) -> TranscriptionResult:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        model = self._load()
        pipeline = self._load_pipeline(model) if (self.use_batched_pipeline and vad_filter) else None
        runner = pipeline.transcribe if pipeline is not None else model.transcribe

        options: dict[str, Any] = {
            "task": task,
            "language": language,
            "initial_prompt": initial_prompt,
            "word_timestamps": True,
            "vad_filter": vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
            "hallucination_silence_threshold": self.hallucination_silence_threshold,
        }
        if pipeline is not None:
            options["batch_size"] = self.batch_size
        options = _supported_kwargs(runner, options)

        segments: list[Segment] = []
        total = 0.0
        last_word_end = 0.0
        # The generator is consumed *inside* the try: faster-whisper decodes
        # lazily, so a codec error or a CUDA fault surfaces on iteration,
        # not on the call -- and must still arrive as a TranscriptionError.
        try:
            raw_segments, info = runner(_load_audio(audio_path), **options)
            total = _as_float(getattr(info, "duration", 0.0))
            for raw_segment in raw_segments:
                if should_stop is not None and should_stop():
                    raise TranscriptionCancelled(f"Transcription of {audio_path.name} was cancelled")
                segment, last_word_end = _convert_segment(raw_segment, floor=last_word_end)
                segments.append(segment)
                if on_progress is not None:
                    done = min(segment.end, total) if total > 0 else segment.end
                    on_progress(done, total)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

        if on_progress is not None and total > 0:
            on_progress(total, total)
        return TranscriptionResult(
            segments=tuple(segments),
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
        on_progress: ProgressCallback | None = None,
        should_stop: StopCheck | None = None,
    ) -> TranscriptionResult:
        return self._run(
            audio_path,
            task="transcribe",
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=vad_filter,
            on_progress=on_progress,
            should_stop=should_stop,
        )

    def translate(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        on_progress: ProgressCallback | None = None,
        should_stop: StopCheck | None = None,
    ) -> TranscriptionResult:
        return self._run(
            audio_path,
            task="translate",
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=vad_filter,
            on_progress=on_progress,
            should_stop=should_stop,
        )



def _load_audio(audio_path: Path) -> "Any":
    """The audio as a float32 array at 16 kHz, or the path when it is not
    the 16 kHz mono PCM WAV our extractor writes.

    Handing faster-whisper an array means it never opens the file through
    PyAV, so the bundle can leave PyAV (and the GPL-built FFmpeg inside
    its wheel) out entirely: the licence question then rests only on the
    separate ffmpeg.exe we call as a process.
    """
    import wave

    import numpy as np

    try:
        with wave.open(str(audio_path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                return str(audio_path)
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError, OSError):
        return str(audio_path)
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _supported_kwargs(func, options: dict[str, Any]) -> dict[str, Any]:
    """Drop options the installed backend's ``transcribe`` does not accept.

    faster-whisper has added and renamed keyword arguments across
    releases; passing one it does not know is a TypeError before any
    audio is read. Anything accepting ``**kwargs`` (a test double, say)
    gets everything.
    """
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return dict(options)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return dict(options)
    return {key: value for key, value in options.items() if key in parameters}


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _convert_segment(raw_segment, *, floor: float = 0.0) -> tuple[Segment, float]:
    """Convert one faster-whisper segment; returns it with the end time of
    its last word, which the next segment uses as its ``floor``.

    Word times are clamped monotonic: faster-whisper emits a word that
    starts before the previous one ended at VAD chunk boundaries, which
    became overlapping SRT cues. ``start`` is pulled up to the previous
    word's end and ``end`` never precedes ``start``.
    """
    words: list[Word] = []
    previous_end = floor
    for raw_word in raw_segment.words or []:
        start = max(_as_float(raw_word.start), previous_end)
        end = max(_as_float(raw_word.end), start)
        words.append(
            Word(
                text=raw_word.word.strip(),
                start=start,
                end=end,
                probability=getattr(raw_word, "probability", 1.0),
            )
        )
        previous_end = end
    segment_start = _as_float(raw_segment.start)
    segment = Segment(
        text=raw_segment.text.strip(),
        start=segment_start,
        end=max(_as_float(raw_segment.end), segment_start),
        words=tuple(words),
    )
    return segment, previous_end
