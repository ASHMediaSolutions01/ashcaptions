"""Tests for ash_captions.engine.transcribe.

faster-whisper is not a real dependency of this test run: a fake module
is injected into sys.modules so WhisperTranscriber can be exercised
(argument construction, error handling, data conversion) without the
real package or a downloaded model.
"""
from __future__ import annotations

import builtins
import sys
import types
from unittest.mock import MagicMock

import pytest

from ash_captions.engine.transcribe import (
    Segment,
    Transcriber,
    TranscriptionCancelled,
    TranscriptionError,
    TranscriptionResult,
    Word,
    WhisperTranscriber,
)


def _fake_word(word, start, end, probability=0.9):
    w = MagicMock()
    w.word = word
    w.start = start
    w.end = end
    w.probability = probability
    return w


def _fake_segment(text, start, end, words):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.words = words
    return seg


def _install_fake_faster_whisper(monkeypatch, model_instance):
    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = MagicMock(return_value=model_instance)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return fake_module


def test_whisper_transcriber_satisfies_transcriber_protocol():
    assert isinstance(WhisperTranscriber(), Transcriber)


def test_transcribe_raises_if_audio_file_missing(tmp_path):
    transcriber = WhisperTranscriber()
    missing = tmp_path / "missing.wav"

    with pytest.raises(TranscriptionError, match="not found"):
        transcriber.transcribe(missing)


def test_transcribe_does_not_import_faster_whisper_when_audio_missing(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    transcriber = WhisperTranscriber()
    missing = tmp_path / "missing.wav"

    with pytest.raises(TranscriptionError):
        transcriber.transcribe(missing)
    assert "faster_whisper" not in sys.modules


def test_transcribe_raises_when_faster_whisper_not_installed(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    transcriber = WhisperTranscriber()

    with pytest.raises(TranscriptionError, match="not installed"):
        transcriber.transcribe(audio)


def test_transcribe_passes_correct_arguments_to_model(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="en", language_probability=0.99))
    _install_fake_faster_whisper(monkeypatch, model)

    transcriber = WhisperTranscriber(model_size="medium", device="cuda")
    transcriber.transcribe(
        audio, language="en", initial_prompt="Ash Media", vad_filter=False
    )

    model.transcribe.assert_called_once()
    args, kwargs = model.transcribe.call_args
    assert args[0] == str(audio)
    assert kwargs["task"] == "transcribe"
    assert kwargs["language"] == "en"
    assert kwargs["initial_prompt"] == "Ash Media"
    assert kwargs["word_timestamps"] is True
    assert kwargs["vad_filter"] is False


def test_translate_uses_translate_task(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="es", language_probability=0.9))
    _install_fake_faster_whisper(monkeypatch, model)

    transcriber = WhisperTranscriber()
    transcriber.translate(audio)

    _, kwargs = model.transcribe.call_args
    assert kwargs["task"] == "translate"


def test_transcribe_converts_segments_and_words(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    raw_words = [_fake_word(" Hello", 0.0, 0.4), _fake_word(" world", 0.4, 0.9)]
    raw_segment = _fake_segment("Hello world", 0.0, 0.9, raw_words)

    model = MagicMock()
    model.transcribe.return_value = ([raw_segment], MagicMock(language="en", language_probability=0.95))
    _install_fake_faster_whisper(monkeypatch, model)

    transcriber = WhisperTranscriber()
    result = transcriber.transcribe(audio)

    assert isinstance(result, TranscriptionResult)
    assert result.language == "en"
    assert result.language_probability == 0.95
    assert len(result.segments) == 1

    segment = result.segments[0]
    assert isinstance(segment, Segment)
    assert segment.text == "Hello world"
    assert len(segment.words) == 2
    assert segment.words[0] == Word(text="Hello", start=0.0, end=0.4, probability=0.9)
    assert segment.words[1].text == "world"

    assert result.words == segment.words


def test_transcribe_wraps_model_call_exception(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    model = MagicMock()
    model.transcribe.side_effect = RuntimeError("boom")
    _install_fake_faster_whisper(monkeypatch, model)

    transcriber = WhisperTranscriber()
    with pytest.raises(TranscriptionError, match="Transcription failed"):
        transcriber.transcribe(audio)


def test_transcribe_wraps_model_load_exception(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = MagicMock(side_effect=RuntimeError("cudnn missing"))
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    transcriber = WhisperTranscriber()
    with pytest.raises(TranscriptionError, match="Failed to load Whisper model"):
        transcriber.transcribe(audio)


def test_model_is_loaded_once_and_reused(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="en", language_probability=0.9))
    fake_module = _install_fake_faster_whisper(monkeypatch, model)

    transcriber = WhisperTranscriber()
    transcriber.transcribe(audio)
    transcriber.translate(audio)

    fake_module.WhisperModel.assert_called_once()


# ---------------------------------------------------------------------------
# long-audio behaviour: batched pipeline, progress, cancellation, clamping
# ---------------------------------------------------------------------------


class _FakePipeline:
    """Stands in for faster_whisper.BatchedInferencePipeline with the real
    keyword set of 1.2 (no **kwargs), so unsupported options would TypeError."""

    created: list["_FakePipeline"] = []

    def __init__(self, model):
        self.model = model
        self.calls: list[dict] = []
        self.segments: list = []
        self.info = MagicMock(language="en", language_probability=0.8, duration=100.0)
        _FakePipeline.created.append(self)

    def transcribe(self, audio, language=None, task="transcribe", initial_prompt=None, word_timestamps=False,
                   vad_filter=True, batch_size=8, condition_on_previous_text=True,
                   hallucination_silence_threshold=None):
        self.calls.append(dict(audio=audio, language=language, task=task, initial_prompt=initial_prompt,
                               word_timestamps=word_timestamps, vad_filter=vad_filter, batch_size=batch_size,
                               condition_on_previous_text=condition_on_previous_text,
                               hallucination_silence_threshold=hallucination_silence_threshold))
        return iter(self.segments), self.info


def _install_fake_with_pipeline(monkeypatch, model_instance):
    fake_module = _install_fake_faster_whisper(monkeypatch, model_instance)
    fake_module.BatchedInferencePipeline = _FakePipeline
    _FakePipeline.created.clear()
    return fake_module


def _audio(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    return audio


def test_uses_the_batched_pipeline_with_bounded_memory_settings(tmp_path, monkeypatch):
    """WhisperModel.transcribe computes one log-mel STFT for the whole file
    (5 GB at 90 minutes); the batched pipeline works per VAD chunk."""
    model = MagicMock()
    _install_fake_with_pipeline(monkeypatch, model)

    transcriber = WhisperTranscriber(batch_size=4)
    result = transcriber.transcribe(_audio(tmp_path), language="es", initial_prompt="Ash")

    model.transcribe.assert_not_called()
    (pipeline,) = _FakePipeline.created
    assert pipeline.model is model
    call = pipeline.calls[0]
    assert call["language"] == "es" and call["task"] == "transcribe" and call["initial_prompt"] == "Ash"
    assert call["word_timestamps"] is True and call["vad_filter"] is True and call["batch_size"] == 4
    assert call["condition_on_previous_text"] is False
    assert call["hallucination_silence_threshold"] == 2.0
    assert result.language == "en"


def test_pipeline_is_built_once_and_reused(tmp_path, monkeypatch):
    _install_fake_with_pipeline(monkeypatch, MagicMock())
    transcriber = WhisperTranscriber()
    transcriber.transcribe(_audio(tmp_path))
    transcriber.translate(_audio(tmp_path))
    assert len(_FakePipeline.created) == 1
    assert [c["task"] for c in _FakePipeline.created[0].calls] == ["transcribe", "translate"]


def test_vad_off_uses_the_eager_model_which_can_run_without_vad(tmp_path, monkeypatch):
    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="en", language_probability=0.9, duration=1.0))
    _install_fake_with_pipeline(monkeypatch, model)
    WhisperTranscriber().transcribe(_audio(tmp_path), vad_filter=False)
    model.transcribe.assert_called_once()
    assert _FakePipeline.created == []


def test_batched_pipeline_can_be_switched_off(tmp_path, monkeypatch):
    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="en", language_probability=0.9, duration=1.0))
    _install_fake_with_pipeline(monkeypatch, model)
    WhisperTranscriber(use_batched_pipeline=False).transcribe(_audio(tmp_path))
    model.transcribe.assert_called_once()
    assert _FakePipeline.created == []


def test_hallucination_controls_reach_the_eager_model_by_default(tmp_path, monkeypatch):
    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="en", language_probability=0.9, duration=1.0))
    _install_fake_faster_whisper(monkeypatch, model)
    WhisperTranscriber().transcribe(_audio(tmp_path))
    kwargs = model.transcribe.call_args[1]
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["hallucination_silence_threshold"] == 2.0
    assert kwargs["vad_filter"] is True


def test_options_the_installed_backend_does_not_accept_are_dropped(tmp_path, monkeypatch):
    """faster-whisper renames keyword arguments across releases; an
    unknown one is a TypeError before any audio is read."""
    seen = {}

    class OldPipeline(_FakePipeline):
        def transcribe(self, audio, language=None, task="transcribe", word_timestamps=False, vad_filter=True):
            seen.update(language=language, task=task, word_timestamps=word_timestamps, vad_filter=vad_filter)
            return iter([]), self.info

    fake_module = _install_fake_faster_whisper(monkeypatch, MagicMock())
    fake_module.BatchedInferencePipeline = OldPipeline
    WhisperTranscriber().transcribe(_audio(tmp_path), language="en", initial_prompt="dropped")
    assert seen == {"language": "en", "task": "transcribe", "word_timestamps": True, "vad_filter": True}


def test_cpu_threads_default_is_capped_and_passed_to_the_model(tmp_path, monkeypatch):
    fake_module = _install_fake_faster_whisper(monkeypatch, MagicMock(
        transcribe=MagicMock(return_value=([], MagicMock(language="en", duration=1.0)))
    ))
    monkeypatch.setattr("ash_captions.engine.transcribe.os.cpu_count", lambda: 64)
    WhisperTranscriber().transcribe(_audio(tmp_path))
    assert fake_module.WhisperModel.call_args[1]["cpu_threads"] == 16
    fake_module.WhisperModel.reset_mock()
    WhisperTranscriber(cpu_threads=6).transcribe(_audio(tmp_path))
    assert fake_module.WhisperModel.call_args[1]["cpu_threads"] == 6


def test_on_progress_is_called_per_segment_with_the_total_from_info(tmp_path, monkeypatch):
    _install_fake_with_pipeline(monkeypatch, MagicMock())
    transcriber = WhisperTranscriber()
    transcriber._load()
    pipeline = transcriber._load_pipeline(transcriber._model)
    pipeline.segments = [
        _fake_segment("one", 0.0, 12.5, [_fake_word(" one", 0.0, 12.5)]),
        _fake_segment("two", 30.0, 47.0, [_fake_word(" two", 30.0, 47.0)]),
    ]
    updates = []
    transcriber.transcribe(_audio(tmp_path), on_progress=lambda done, total: updates.append((done, total)))
    assert updates == [(12.5, 100.0), (47.0, 100.0), (100.0, 100.0)]


def test_should_stop_cancels_between_segments(tmp_path, monkeypatch):
    _install_fake_with_pipeline(monkeypatch, MagicMock())
    transcriber = WhisperTranscriber()
    transcriber._load()
    pipeline = transcriber._load_pipeline(transcriber._model)
    consumed = []

    def segments():
        for i in range(10):
            consumed.append(i)
            yield _fake_segment(f"s{i}", i * 10.0, i * 10.0 + 5, [_fake_word(f" s{i}", i * 10.0, i * 10.0 + 5)])

    pipeline.segments = segments()
    polls = []

    def should_stop():
        polls.append(1)
        return len(polls) == 3

    with pytest.raises(TranscriptionCancelled):
        transcriber.transcribe(_audio(tmp_path), should_stop=should_stop)
    assert consumed == [0, 1, 2]


def test_errors_raised_while_decoding_are_wrapped(tmp_path, monkeypatch):
    """faster-whisper decodes lazily: the failure surfaces on iteration,
    not on the call, and used to escape the TranscriptionError wrapper."""
    _install_fake_with_pipeline(monkeypatch, MagicMock())
    transcriber = WhisperTranscriber()
    transcriber._load()
    pipeline = transcriber._load_pipeline(transcriber._model)

    def segments():
        yield _fake_segment("ok", 0.0, 1.0, [])
        raise RuntimeError("CUDA fell over")

    pipeline.segments = segments()
    with pytest.raises(TranscriptionError, match="CUDA fell over"):
        transcriber.transcribe(_audio(tmp_path))


def test_word_timestamps_are_clamped_monotonic_across_segments(tmp_path, monkeypatch):
    """At VAD chunk boundaries faster-whisper emits a word starting before
    the previous one ended, which became overlapping SRT cues."""
    _install_fake_with_pipeline(monkeypatch, MagicMock())
    transcriber = WhisperTranscriber()
    transcriber._load()
    pipeline = transcriber._load_pipeline(transcriber._model)
    pipeline.segments = [
        _fake_segment("a b", 0.0, 2.0, [_fake_word(" a", 0.0, 1.0), _fake_word(" b", 0.8, 1.6)]),
        _fake_segment("c d", 1.5, 3.0, [_fake_word(" c", 1.4, 1.5), _fake_word(" d", 2.0, 1.9)]),
    ]
    words = transcriber.transcribe(_audio(tmp_path)).words
    assert [(w.start, w.end) for w in words] == [(0.0, 1.0), (1.0, 1.6), (1.6, 1.6), (2.0, 2.0)]
    assert all(later.start >= earlier.end for earlier, later in zip(words, words[1:], strict=False))


def test_cancellation_is_a_transcription_error_and_defaults_keep_old_fakes_working():
    assert issubclass(TranscriptionCancelled, TranscriptionError)

    class OldFake:
        def transcribe(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
            return TranscriptionResult(segments=(), language="en")

        def translate(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
            return TranscriptionResult(segments=(), language="en")

    assert isinstance(OldFake(), Transcriber)


# ---------------------------------------------------------------------------
# model loading: offline cache and CPU fallback
# ---------------------------------------------------------------------------


def _loaded_model():
    model = MagicMock()
    model.transcribe.return_value = ([], MagicMock(language="en", language_probability=0.9, duration=1.0))
    return model


def test_local_files_only_reaches_the_model(tmp_path, monkeypatch):
    """A bundled HF cache must be used as-is: without this every launch
    asks the Hub for a newer revision and re-downloads it."""
    fake_module = _install_fake_faster_whisper(monkeypatch, _loaded_model())
    WhisperTranscriber(local_files_only=True, download_root=tmp_path).transcribe(_audio(tmp_path))
    kwargs = fake_module.WhisperModel.call_args[1]
    assert kwargs["local_files_only"] is True
    assert kwargs["download_root"] == str(tmp_path)
    fake_module.WhisperModel.reset_mock()
    WhisperTranscriber().transcribe(_audio(tmp_path))
    assert fake_module.WhisperModel.call_args[1]["local_files_only"] is False


def test_effective_device_reports_where_the_model_loaded(tmp_path, monkeypatch):
    _install_fake_faster_whisper(monkeypatch, _loaded_model())
    transcriber = WhisperTranscriber(device="cpu")
    assert transcriber.effective_device is None
    transcriber.transcribe(_audio(tmp_path))
    assert (transcriber.effective_device, transcriber.effective_compute_type) == ("cpu", "int8")


@pytest.mark.parametrize("requested", ["cuda", "auto"])
def test_gpu_load_failure_falls_back_to_the_cpu_once(tmp_path, monkeypatch, caplog, requested):
    """Missing cuBLAS/cuDNN DLLs or a driver mismatch surface as an
    exception from WhisperModel(); the job must still run, on the CPU."""
    model = _loaded_model()

    def construct(_size, **kwargs):
        if kwargs["device"] != "cpu":
            raise RuntimeError("Library cublas64_12.dll is not found")
        return model

    fake_module = _install_fake_faster_whisper(monkeypatch, model)
    fake_module.WhisperModel = MagicMock(side_effect=construct)
    transcriber = WhisperTranscriber(device=requested)

    with caplog.at_level("WARNING", logger="ash_captions.engine.transcribe"):
        result = transcriber.transcribe(_audio(tmp_path))

    assert result.language == "en"
    calls = [c[1] for c in fake_module.WhisperModel.call_args_list]
    assert [c["device"] for c in calls] == [requested, "cpu"]
    assert calls[1]["compute_type"] == "int8"
    assert calls[1]["cpu_threads"] == transcriber.cpu_threads
    assert (transcriber.effective_device, transcriber.effective_compute_type) == ("cpu", "int8")
    assert transcriber.device == requested  # the request is preserved for the UI
    assert "cublas64_12.dll" in caplog.text and requested in caplog.text
    # loaded once: a later call must not construct again
    transcriber.transcribe(_audio(tmp_path))
    assert fake_module.WhisperModel.call_count == 2


def test_failure_on_both_devices_is_one_transcription_error(tmp_path, monkeypatch):
    fake_module = _install_fake_faster_whisper(monkeypatch, _loaded_model())
    fake_module.WhisperModel = MagicMock(side_effect=[RuntimeError("no cuda"), OSError("no cpu either")])
    transcriber = WhisperTranscriber(device="cuda")
    with pytest.raises(TranscriptionError, match="no cuda.*no cpu either"):
        transcriber.transcribe(_audio(tmp_path))
    assert transcriber.effective_device is None
    assert fake_module.WhisperModel.call_count == 2


def test_cpu_load_failure_is_not_retried(tmp_path, monkeypatch):
    fake_module = _install_fake_faster_whisper(monkeypatch, _loaded_model())
    fake_module.WhisperModel = MagicMock(side_effect=RuntimeError("bad model dir"))
    with pytest.raises(TranscriptionError, match="on cpu: bad model dir"):
        WhisperTranscriber(device="cpu").transcribe(_audio(tmp_path))
    assert fake_module.WhisperModel.call_count == 1
