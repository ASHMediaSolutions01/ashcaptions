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
