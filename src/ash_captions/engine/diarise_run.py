"""Run the speaker model over a file and hand back speaker turns.

``diarise.py`` holds the decisions; this fetches the audio, runs the VAD
that already ships with faster-whisper, embeds each speech span with the
WeSpeaker model, and asks ``diarise`` to split them.

Nothing new is needed at run time beyond the model file: onnxruntime is
already here for the person matte, numpy for everything, and the Silero
VAD ships inside faster-whisper. The model is 26 MB and downloads once,
the same way the matte model does.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import DEFAULT_FFMPEG_PATH
from .diarise import (
    MIN_FRAMES,
    MIN_TURN_SECONDS,
    SAMPLE_RATE,
    Diarisation,
    DiarisationError,
    fbank,
    mel_filters,
    normalise,
    one_speaker_only,
    separation,
    split_two,
    turns_from_labels,
)
from .ffmpeg_process import no_window_flags

log = logging.getLogger(__name__)

SPEAKER_MODEL_FILENAME = "voxceleb_resnet34_LM.onnx"
SPEAKER_MODEL_URL = (
    "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/"
    + SPEAKER_MODEL_FILENAME
)
# The real file is ~26 MB; anything much smaller is an HTML error page,
# which is the failure the matte model fetch already learned to catch.
SPEAKER_MODEL_MIN_BYTES = 20_000_000

ProgressCallback = Callable[[float], None]
StopCheck = Callable[[], bool]


def speaker_model_path(models_dir: Path | str) -> Path:
    return Path(models_dir) / SPEAKER_MODEL_FILENAME


def ensure_speaker_model(
    models_dir: Path | str, *, download: bool = True, timeout: float = 300
) -> Path:
    """The speaker model, downloaded on first use, refusing plainly offline."""
    path = speaker_model_path(models_dir)
    if path.is_file() and path.stat().st_size >= SPEAKER_MODEL_MIN_BYTES:
        return path
    if not download:
        raise DiarisationError(
            f"Speaker labels need {SPEAKER_MODEL_FILENAME}, which is not in {models_dir}."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(SPEAKER_MODEL_URL, timeout=timeout) as response:
            partial.write_bytes(response.read())
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise DiarisationError(
            "Could not download the speaker model (needed once, 26 MB). "
            f"Check the connection and try again: {exc}"
        ) from exc
    if partial.stat().st_size < SPEAKER_MODEL_MIN_BYTES:
        partial.unlink(missing_ok=True)
        raise DiarisationError(
            "The speaker model downloaded incomplete; try again when the connection is steady."
        )
    partial.replace(path)
    return path


def read_audio(
    media_path: Path | str, *, ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH
) -> np.ndarray:
    """The whole soundtrack as 16 kHz mono floats in [-1, 1].

    Through a temporary .wav rather than a pipe: ffmpeg cannot write a
    valid RIFF header to a stream it cannot seek, and a headerless pipe
    read as int16 is exactly the kind of thing that half-works.
    """
    media_path = Path(media_path)
    with tempfile.TemporaryDirectory(prefix="ash-diar-") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        command = [
            str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(media_path),
            "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "pcm_s16le", str(wav_path),
        ]
        try:
            done = subprocess.run(command, capture_output=True, **no_window_flags())
        except OSError as exc:
            raise DiarisationError(f"Could not run ffmpeg to read the audio: {exc}") from exc
        if done.returncode != 0 or not wav_path.is_file():
            detail = done.stderr.decode("utf-8", "replace")[-300:]
            raise DiarisationError(f"Could not read audio from {media_path.name}. {detail}".strip())
        with wave.open(str(wav_path)) as fh:
            raw = fh.readframes(fh.getnframes())
    if not raw:
        raise DiarisationError(f"{media_path.name} has no audio to tell speakers apart with.")
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def speech_spans(signal: np.ndarray) -> list[tuple[float, float]]:
    """Where there is speech, from the VAD faster-whisper already ships."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError as exc:  # pragma: no cover - faster-whisper is a hard dependency
        raise DiarisationError(f"The voice-activity detector is not available: {exc}") from exc
    options = VadOptions(min_speech_duration_ms=700, min_silence_duration_ms=400)
    stamps = get_speech_timestamps(signal, options)
    return [(s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE) for s in stamps]


class _Embedder:
    def __init__(self, model_path: Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - ships with the matte
            raise DiarisationError(f"onnxruntime is not available: {exc}") from exc
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._filters = mel_filters()

    def __call__(self, chunk: np.ndarray) -> np.ndarray | None:
        feats = fbank(chunk, self._filters)
        if feats is None or len(feats) < MIN_FRAMES:
            return None
        out = self._session.run(None, {"feats": feats[None, :, :]})[0][0]
        return normalise(out)


def diarise_media(
    media_path: Path | str,
    *,
    models_dir: Path | str,
    ffmpeg_path: Path | str = DEFAULT_FFMPEG_PATH,
    min_turn_seconds: float = MIN_TURN_SECONDS,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> Diarisation:
    """Speaker turns for ``media_path``.

    Returns an empty ``Diarisation`` when there is only one voice --
    which is a real answer, not a failure. Forcing two clusters on a
    monologue produces a speaker change in the middle of one person
    talking, and a caption that says so is worse than no label at all.
    """
    model_path = ensure_speaker_model(models_dir)
    signal = read_audio(media_path, ffmpeg_path=ffmpeg_path)
    spans = [s for s in speech_spans(signal) if s[1] - s[0] >= min_turn_seconds]
    log.info("diarise: %d speech span(s) of %.1fs or more", len(spans), min_turn_seconds)
    if len(spans) < 4:
        log.info("diarise: too little separated speech to tell voices apart")
        return Diarisation()

    embed = _Embedder(model_path)
    vectors, kept = [], []
    for index, (start, end) in enumerate(spans):
        if should_stop is not None and should_stop():
            raise DiarisationError("Telling the speakers apart was cancelled")
        vector = embed(signal[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
        if vector is not None:
            vectors.append(vector)
            kept.append((start, end))
        if on_progress is not None:
            on_progress(min(100.0, 100.0 * (index + 1) / len(spans)))
    if len(vectors) < 4:
        return Diarisation()

    matrix = np.array(vectors, dtype=np.float32)
    labels = split_two(matrix)
    score = separation(matrix, labels)
    log.info("diarise: separation %.3f across %d turns", score, len(kept))
    if one_speaker_only(score):
        log.info("diarise: one voice only (separation %.3f); no labels", score)
        return Diarisation()
    if on_progress is not None:
        on_progress(100.0)
    return Diarisation(turns=turns_from_labels(kept, labels))
