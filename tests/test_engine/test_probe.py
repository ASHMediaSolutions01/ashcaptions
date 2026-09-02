"""Tests for ash_captions.engine.probe.

No real ffprobe is invoked: subprocess.run is mocked with the JSON ffprobe
would print.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ash_captions.engine.probe import (
    FALLBACK_FPS,
    PROBE_TIMEOUT_SECONDS,
    ProbeError,
    VideoInfo,
    ffprobe_beside,
    probe_video,
    sane_fps,
)


def _payload(*, fps="30/1", audio="aac", duration="20.5", streams=None):
    if streams is None:
        streams = [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "avg_frame_rate": fps},
        ]
        if audio is not None:
            streams.append({"codec_type": "audio", "codec_name": audio, "avg_frame_rate": "0/0"})
    return json.dumps({"streams": streams, "format": {"duration": duration}})


def _probe(tmp_path, stdout, returncode=0):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    with patch("ash_captions.engine.probe.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
        info = probe_video(video, ffprobe_path=tmp_path / "ffprobe.exe")
    return info, mock_run


def test_reads_size_fps_duration_and_audio_codec(tmp_path):
    info, _ = _probe(tmp_path, _payload())
    assert info == VideoInfo(width=1080, height=1920, fps=30.0, duration_seconds=20.5, audio_codec="aac")


def test_finds_the_video_stream_even_when_audio_is_listed_first(tmp_path):
    streams = [
        {"codec_type": "audio", "codec_name": "pcm_s16le"},
        {"codec_type": "video", "codec_name": "prores", "width": 3840, "height": 2160, "avg_frame_rate": "25/1"},
    ]
    info, _ = _probe(tmp_path, _payload(streams=streams))
    assert (info.width, info.height, info.fps, info.audio_codec) == (3840, 2160, 25.0, "pcm_s16le")


def test_no_audio_stream_means_audio_codec_none(tmp_path):
    info, _ = _probe(tmp_path, _payload(audio=None))
    assert info.audio_codec is None


def test_uses_avg_frame_rate_not_r_frame_rate(tmp_path):
    """r_frame_rate lies on VFR phone footage; it must not even be requested."""
    _, mock_run = _probe(tmp_path, _payload(fps="125/7"))
    argv = mock_run.call_args[0][0]
    entries = argv[argv.index("-show_entries") + 1]
    assert "avg_frame_rate" in entries
    assert "r_frame_rate" not in entries


@pytest.mark.parametrize("bogus", ["1000/1", "90000/1", "0/0", "-25/1", "abc", "121/1", "nan"])
def test_implausible_frame_rates_fall_back_to_30(tmp_path, bogus):
    info, _ = _probe(tmp_path, _payload(fps=bogus))
    assert info.fps == FALLBACK_FPS


@pytest.mark.parametrize(
    ("value", "expected"),
    [("125/7", 125 / 7), ("120000/1001", 120000 / 1001), ("24000/1001", 24000 / 1001), ("60/1", 60.0)],
)
def test_plausible_frame_rates_are_kept(tmp_path, value, expected):
    info, _ = _probe(tmp_path, _payload(fps=value))
    assert info.fps == pytest.approx(expected)


def test_sane_fps_bounds():
    assert sane_fps(0.0) == FALLBACK_FPS
    assert sane_fps(120.0) == 120.0
    assert sane_fps(120.01) == FALLBACK_FPS
    assert sane_fps(float("inf")) == FALLBACK_FPS


def test_probe_timeout_is_generous_and_window_is_hidden(tmp_path):
    _, mock_run = _probe(tmp_path, _payload())
    kwargs = mock_run.call_args[1]
    assert kwargs["timeout"] == PROBE_TIMEOUT_SECONDS == 180
    assert kwargs["stdin"] is subprocess.DEVNULL
    if sys.platform == "win32":
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_missing_video_raises_without_running_ffprobe(tmp_path):
    with patch("ash_captions.engine.probe.subprocess.run") as mock_run:
        with pytest.raises(ProbeError, match="not found"):
            probe_video(tmp_path / "missing.mp4", ffprobe_path=tmp_path / "ffprobe.exe")
    mock_run.assert_not_called()


def test_nonzero_exit_raises(tmp_path):
    with pytest.raises(ProbeError, match="exit 1"):
        _probe(tmp_path, "", returncode=1)


def test_launch_failure_raises(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    with patch("ash_captions.engine.probe.subprocess.run", side_effect=FileNotFoundError("no ffprobe")):
        with pytest.raises(ProbeError, match="Could not run ffprobe"):
            probe_video(video, ffprobe_path=tmp_path / "ffprobe.exe")


def test_no_video_stream_raises(tmp_path):
    with pytest.raises(ProbeError, match="no usable video stream"):
        _probe(tmp_path, _payload(streams=[{"codec_type": "audio", "codec_name": "aac"}]))


def test_garbage_output_raises(tmp_path):
    with pytest.raises(ProbeError):
        _probe(tmp_path, "not json")


def test_ffprobe_beside_maps_ffmpeg_to_its_sibling():
    assert ffprobe_beside(Path("C:/app/bin/ffmpeg.exe")) == Path("C:/app/bin/ffprobe.exe")
    assert ffprobe_beside("ffmpeg") == Path("ffprobe")


def test_video_info_keeps_positional_construction():
    """The runner and its tests build VideoInfo positionally; the new
    audio field must stay optional and last."""
    info = VideoInfo(1080, 1920, 30.0, 60.0)
    assert info.audio_codec is None
    assert info.size_arg == "1080x1920"
