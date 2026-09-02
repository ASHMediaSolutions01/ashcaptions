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


def test_build_burn_command_omits_fontsdir_by_default(tmp_path):
    args = build_burn_command(
        tmp_path / "in.mp4", tmp_path / "subs.ass", tmp_path / "out.mp4", ffmpeg_path=tmp_path / "ffmpeg.exe"
    )
    vf = args[args.index("-vf") + 1]
    assert "fontsdir" not in vf


def test_build_burn_command_with_fontsdir_none_is_byte_identical_to_omitting_it(tmp_path):
    with_none = build_burn_command(
        "C:/videos/in.mp4", r"C:\subs\out.ass", "C:/videos/out.mp4", ffmpeg_path="bin/ffmpeg.exe", fontsdir=None
    )
    without_param = build_burn_command(
        "C:/videos/in.mp4", r"C:\subs\out.ass", "C:/videos/out.mp4", ffmpeg_path="bin/ffmpeg.exe"
    )
    assert with_none == without_param


def test_build_burn_command_emits_fontsdir_in_the_ass_filter():
    args = build_burn_command(
        "C:/videos/in.mp4",
        "C:/videos/subs.ass",
        "C:/videos/out.mp4",
        ffmpeg_path="bin/ffmpeg.exe",
        fontsdir=r"C:\AshCaptions\assets\fonts",
    )
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("ass='C\\:/videos/subs.ass':fontsdir='")
    assert "AshCaptions/assets/fonts" in vf


def test_build_burn_command_escapes_colons_and_backslashes_in_fontsdir():
    args = build_burn_command(
        "in.mp4", "subs.ass", "out.mp4", ffmpeg_path="ffmpeg.exe", fontsdir=r"C:\Program Files\Ash Captions\fonts"
    )
    vf = args[args.index("-vf") + 1]
    assert vf == (
        r"ass='subs.ass':fontsdir='C\:/Program Files/Ash Captions/fonts'"
    )


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
    # Iterable, not .read(): burn_captions drains stderr line-by-line on a
    # background thread, because reading it only after the stdout loop
    # deadlocks against ffmpeg once the pipe buffer fills.
    process.stderr = iter([stderr] if stderr else [])
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
    # Exactly one *burn* invocation. Popen is also used to probe the binary
    # for available encoders (an LGPL ffmpeg has no libx264), so asserting a
    # single Popen call overall would be asserting an implementation detail.
    burn_calls = [c for c in mock_popen.call_args_list if "-encoders" not in c[0][0]]
    assert len(burn_calls) == 1
    assert output.parent.is_dir()


def test_burn_captions_threads_fontsdir_into_the_command(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]")
    fonts_dir = tmp_path / "assets" / "fonts"

    fake_process = _make_fake_process([], returncode=0)

    with patch("ash_captions.engine.burn.subprocess.Popen", return_value=fake_process) as mock_popen, patch(
        "ash_captions.engine.burn.detect_nvenc", return_value=False
    ):
        burn_captions(
            video, ass, tmp_path / "out.mp4", duration_seconds=10.0, ffmpeg_path=tmp_path / "ffmpeg.exe",
            fontsdir=fonts_dir,
        )

    called_args = mock_popen.call_args[0][0]
    vf = called_args[called_args.index("-vf") + 1]
    assert "fontsdir=" in vf
    assert "fonts" in vf


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


def test_selects_a_software_encoder_the_build_actually_has(monkeypatch, tmp_path):
    """An LGPL ffmpeg has no libx264 -- x264 is GPL, so the LGPL build is
    configured --disable-libx264. Burning hardcoded libx264 failed outright
    with "Unknown encoder". The encoder must come from the real binary."""
    from ash_captions.engine import burn

    burn._encoder_cache.clear()
    monkeypatch.setattr(
        burn, "available_encoders", lambda _p: frozenset({"libopenh264", "h264_mf"})
    )
    assert burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=False) == "libopenh264"


def test_prefers_libx264_when_the_build_has_it(monkeypatch, tmp_path):
    from ash_captions.engine import burn

    burn._encoder_cache.clear()
    monkeypatch.setattr(
        burn, "available_encoders", lambda _p: frozenset({"libx264", "libopenh264"})
    )
    assert burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=False) == "libx264"


def test_raises_a_named_error_when_no_h264_encoder_exists(monkeypatch, tmp_path):
    """Better than letting ffmpeg fail later with its own vaguer message."""
    from ash_captions.engine import burn

    burn._encoder_cache.clear()
    monkeypatch.setattr(burn, "available_encoders", lambda _p: frozenset({"vp9", "aac"}))
    with pytest.raises(burn.BurnInError, match="no usable H.264 encoder"):
        burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=False)


def test_probe_failure_falls_back_rather_than_blocking_a_burn(tmp_path):
    """Probing is a convenience: a missing binary must not stop us building
    a command, or every mocked test and offline run would break."""
    from ash_captions.engine import burn

    burn._encoder_cache.clear()
    assert burn.available_encoders(tmp_path / "definitely-not-here.exe") == frozenset()
    assert burn.select_video_encoder(tmp_path / "definitely-not-here.exe") == "libx264"
