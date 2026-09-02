"""Tests for ash_captions.engine.audio.

No real ffmpeg is invoked: subprocess.run is mocked throughout, so these
pass without ffmpeg installed.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from ash_captions.engine.audio import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE_HZ,
    AudioExtractionError,
    extract_audio,
)


def test_raises_if_input_video_missing(tmp_path):
    missing = tmp_path / "does-not-exist.mp4"
    output = tmp_path / "out.wav"

    with pytest.raises(AudioExtractionError, match="not found"):
        extract_audio(missing, output, ffmpeg_path=tmp_path / "ffmpeg.exe")


def test_does_not_invoke_ffmpeg_when_input_missing(tmp_path):
    missing = tmp_path / "does-not-exist.mp4"
    output = tmp_path / "out.wav"

    with patch("ash_captions.engine.audio.subprocess.run") as mock_run:
        with pytest.raises(AudioExtractionError):
            extract_audio(missing, output, ffmpeg_path=tmp_path / "ffmpeg.exe")
    mock_run.assert_not_called()


def test_builds_correct_ffmpeg_arguments(tmp_path):
    video = tmp_path / "clip with spaces & çafé.mp4"
    video.write_bytes(b"not a real video")
    output = tmp_path / "out" / "audio.wav"
    ffmpeg = tmp_path / "bin" / "ffmpeg.exe"

    with patch("ash_captions.engine.audio.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = extract_audio(video, output, ffmpeg_path=ffmpeg)

    assert result == output
    args, kwargs = mock_run.call_args
    called_argv = args[0]
    assert called_argv[0] == str(ffmpeg)
    assert str(video) in called_argv
    assert str(output) in called_argv
    assert "-ac" in called_argv and called_argv[called_argv.index("-ac") + 1] == str(TARGET_CHANNELS)
    assert "-ar" in called_argv and called_argv[called_argv.index("-ar") + 1] == str(TARGET_SAMPLE_RATE_HZ)
    assert "-vn" in called_argv
    assert kwargs.get("capture_output") is True
    # output parent directory must exist even though ffmpeg itself is mocked
    assert output.parent.is_dir()


def test_raises_typed_error_with_stderr_on_nonzero_exit(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    output = tmp_path / "out.wav"
    ffmpeg = tmp_path / "ffmpeg.exe"

    with patch("ash_captions.engine.audio.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Invalid data found when processing input"
        )
        with pytest.raises(AudioExtractionError) as exc_info:
            extract_audio(video, output, ffmpeg_path=ffmpeg)

    assert exc_info.value.returncode == 1
    assert "Invalid data found" in exc_info.value.stderr


def test_raises_typed_error_when_ffmpeg_binary_missing(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    output = tmp_path / "out.wav"
    ffmpeg = tmp_path / "nonexistent" / "ffmpeg.exe"

    with patch("ash_captions.engine.audio.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("no such file")
        with pytest.raises(AudioExtractionError, match="not found"):
            extract_audio(video, output, ffmpeg_path=ffmpeg)


def test_raises_typed_error_on_os_error_launching_ffmpeg(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    output = tmp_path / "out.wav"
    ffmpeg = tmp_path / "ffmpeg.exe"

    with patch("ash_captions.engine.audio.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("access denied")
        with pytest.raises(AudioExtractionError, match="Failed to launch"):
            extract_audio(video, output, ffmpeg_path=ffmpeg)


def test_runs_headless_and_takes_only_the_first_audio_stream(tmp_path):
    """A tray app has no console for ffmpeg to poll, and a camera file with
    two audio tracks must not have them chosen by "best"."""
    import subprocess as sp
    import sys

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    output = tmp_path / "out.wav"

    with patch("ash_captions.engine.audio.subprocess.run") as mock_run:
        mock_run.return_value = sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        extract_audio(video, output, ffmpeg_path=tmp_path / "ffmpeg.exe")

    argv, kwargs = mock_run.call_args[0][0], mock_run.call_args[1]
    for flag in ("-nostdin", "-nostats", "-hide_banner"):
        assert flag in argv
    assert argv[argv.index("-loglevel") + 1] == "error"
    assert argv[argv.index("-map") + 1] == "0:a:0"
    assert argv.index("-i") < argv.index("-map")  # an input option must precede the output
    assert kwargs["stdin"] is sp.DEVNULL
    if sys.platform == "win32":
        assert kwargs["creationflags"] == sp.CREATE_NO_WINDOW
