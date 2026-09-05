"""Audio reaches faster-whisper as a numpy array, never through PyAV."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from ash_captions.engine import transcribe


def _wav(path: Path, *, rate=16000, channels=1, width=2, seconds=0.5):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(rate * seconds) * channels)
    return path


def test_our_16k_mono_wav_loads_as_float32(tmp_path):
    audio = transcribe._load_audio(_wav(tmp_path / "a.wav"))
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert audio.shape == (8000,)
    assert 0.12 < float(audio[0]) < 0.13  # 0x1000 / 32768


def test_other_formats_fall_back_to_the_path(tmp_path):
    stereo = _wav(tmp_path / "s.wav", channels=2)
    assert transcribe._load_audio(stereo) == str(stereo)
    not_wav = tmp_path / "x.wav"
    not_wav.write_bytes(b"not a wav")
    assert transcribe._load_audio(not_wav) == str(not_wav)


def test_the_bundle_av_stub_satisfies_the_import_and_refuses_use(tmp_path):
    import importlib.util

    stub_dir = Path(__file__).resolve().parents[2] / "scripts" / "pkgtools" / "av_stub" / "av"
    spec = importlib.util.spec_from_file_location("av_stub_for_test", stub_dir / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__version__.endswith("ash-stub")
    try:
        _ = module.open
    except RuntimeError as exc:
        assert "ffmpeg.exe" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the stub must refuse real use")
