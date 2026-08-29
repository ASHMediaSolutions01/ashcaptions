"""Tests for ash_captions.engine.burn.

No real ffmpeg or nvidia-smi is invoked: subprocess is mocked throughout.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ash_captions.engine.burn import (
    BurnInError,
    _parse_progress_line,
    build_burn_command,
    burn_captions,
    detect_nvenc,
)


# ---------------------------------------------------------------------------
# detect_nvenc
# ---------------------------------------------------------------------------


def test_detect_nvenc_true_when_nvidia_smi_succeeds():
    with patch("ash_captions.engine.burn.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert detect_nvenc() is True


def test_detect_nvenc_false_when_nvidia_smi_fails():
    with patch("ash_captions.engine.burn.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        assert detect_nvenc() is False


def test_detect_nvenc_false_when_nvidia_smi_missing():
    with patch("ash_captions.engine.burn.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        assert detect_nvenc() is False


def test_detect_nvenc_false_on_timeout():
    with patch("ash_captions.engine.burn.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)
        assert detect_nvenc() is False


# ---------------------------------------------------------------------------
# build_burn_command
# ---------------------------------------------------------------------------


def test_build_burn_command_uses_libx264_by_default(tmp_path):
    args = build_burn_command(
        tmp_path / "in.mp4", tmp_path / "subs.ass", tmp_path / "out.mp4", ffmpeg_path=tmp_path / "ffmpeg.exe"
    )
    assert "-c:v" in args
    assert args[args.index("-c:v") + 1] == "libx264"


def test_build_burn_command_uses_nvenc_when_requested(tmp_path):
    args = build_burn_command(
        tmp_path / "in.mp4",
        tmp_path / "subs.ass",
        tmp_path / "out.mp4",
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        use_nvenc=True,
    )
    assert args[args.index("-c:v") + 1] == "h264_nvenc"


def test_build_burn_command_escapes_colons_and_backslashes_in_subtitle_path():
    args = build_burn_command(
        "C:/videos/in.mp4",
        r"C:\Users\editor\out dir\subs.ass",
        "C:/videos/out.mp4",
        ffmpeg_path="bin/ffmpeg.exe",
    )
    vf = args[args.index("-vf") + 1]
    assert vf == r"ass='C\:/Users/editor/out dir/subs.ass'"


def test_build_burn_command_reports_progress_via_stdout_pipe(tmp_path):
    args = build_burn_command(
        tmp_path / "in.mp4", tmp_path / "subs.ass", tmp_path / "out.mp4", ffmpeg_path=tmp_path / "ffmpeg.exe"
    )
    assert "-progress" in args
    assert args[args.index("-progress") + 1] == "pipe:1"


# ---------------------------------------------------------------------------
# _parse_progress_line
# ---------------------------------------------------------------------------


def test_parse_progress_line_out_time_us():
    assert _parse_progress_line("out_time_us=5000000", duration_seconds=10.0) == 50.0


def test_parse_progress_line_out_time_ms():
    assert _parse_progress_line("out_time_ms=5000000", duration_seconds=10.0) == 50.0


def test_parse_progress_line_clamps_to_100():
    assert _parse_progress_line("out_time_us=999999999", duration_seconds=10.0) == 100.0


def test_parse_progress_line_ignores_unrelated_lines():
    assert _parse_progress_line("frame=120", duration_seconds=10.0) is None


def test_parse_progress_line_returns_none_for_zero_duration():
    assert _parse_progress_line("out_time_us=1000000", duration_seconds=0.0) is None


# ---------------------------------------------------------------------------
# burn_captions
# ---------------------------------------------------------------------------


def _make_fake_process(progress_lines, returncode=0, stderr=""):
    process = MagicMock()
    process.stdout = iter(progress_lines)
    process.stderr = MagicMock()
    process.stderr.read.return_value = stderr
    process.wait.return_value = returncode
    return process


def test_burn_captions_raises_if_video_missing(tmp_path):
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]")

    with pytest.raises(BurnInError, match="Input video not found"):
        burn_captions(tmp_path / "missing.mp4", ass, tmp_path / "out.mp4", duration_seconds=10.0)


def test_burn_captions_raises_if_ass_missing(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")

    with pytest.raises(BurnInError, match="Subtitle file not found"):
        burn_captions(video, tmp_path / "missing.ass", tmp_path / "out.mp4", duration_seconds=10.0)


def test_burn_captions_does_not_invoke_ffmpeg_when_inputs_missing(tmp_path):
    with patch("ash_captions.engine.burn.subprocess.Popen") as mock_popen:
        with pytest.raises(BurnInError):
            burn_captions(tmp_path / "missing.mp4", tmp_path / "missing.ass", tmp_path / "out.mp4", duration_seconds=10.0)
    mock_popen.assert_not_called()


def test_burn_captions_reports_progress_and_returns_output_path(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]")
    output = tmp_path / "out" / "captioned.mp4"

    fake_process = _make_fake_process(["out_time_us=2500000\n", "out_time_us=5000000\n"], returncode=0)

    progress_updates = []
    with patch("ash_captions.engine.burn.subprocess.Popen", return_value=fake_process) as mock_popen, patch(
        "ash_captions.engine.burn.detect_nvenc", return_value=False
    ):
        result = burn_captions(
            video, ass, output, duration_seconds=10.0, ffmpeg_path=tmp_path / "ffmpeg.exe",
            on_progress=progress_updates.append,
        )

    assert result == output
    assert progress_updates == [25.0, 50.0]
    mock_popen.assert_called_once()
    assert output.parent.is_dir()


def test_burn_captions_auto_detects_nvenc(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]")

    fake_process = _make_fake_process([], returncode=0)

    with patch("ash_captions.engine.burn.subprocess.Popen", return_value=fake_process) as mock_popen, patch(
        "ash_captions.engine.burn.detect_nvenc", return_value=True
    ):
        burn_captions(video, ass, tmp_path / "out.mp4", duration_seconds=10.0, ffmpeg_path=tmp_path / "ffmpeg.exe")

    called_args = mock_popen.call_args[0][0]
    assert called_args[called_args.index("-c:v") + 1] == "h264_nvenc"


def test_burn_captions_raises_typed_error_with_stderr_on_failure(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]")

    fake_process = _make_fake_process([], returncode=1, stderr="Unknown encoder 'h264_nvenc'")

    with patch("ash_captions.engine.burn.subprocess.Popen", return_value=fake_process), patch(
        "ash_captions.engine.burn.detect_nvenc", return_value=False
    ):
        with pytest.raises(BurnInError) as exc_info:
            burn_captions(video, ass, tmp_path / "out.mp4", duration_seconds=10.0, ffmpeg_path=tmp_path / "ffmpeg.exe")

    assert exc_info.value.returncode == 1
    assert "Unknown encoder" in exc_info.value.stderr


def test_burn_captions_raises_typed_error_when_ffmpeg_missing(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]")

    with patch("ash_captions.engine.burn.subprocess.Popen", side_effect=OSError("not found")):
        with pytest.raises(BurnInError, match="Failed to launch"):
            burn_captions(video, ass, tmp_path / "out.mp4", duration_seconds=10.0, ffmpeg_path=tmp_path / "missing.exe")
