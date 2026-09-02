"""Tests for running a burn: ash_captions.engine.burn.burn_captions and
ash_captions.engine.ffmpeg_process.

ffmpeg itself is never run. Where a live process matters (cancellation,
the kill registry) a real child process -- python sleeping -- stands in.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ash_captions.engine import burn, ffmpeg_process
from ash_captions.engine.burn import BurnCancelled, BurnInError, burn_captions, part_path_for
from ash_captions.engine.ffmpeg_process import active_processes, kill_active_processes, run_ffmpeg
from ash_captions.engine.probe import VideoInfo

POPEN = "ash_captions.engine.ffmpeg_process.subprocess.Popen"


@pytest.fixture(autouse=True)
def _no_real_probing(monkeypatch):
    """Patching Popen patches it for subprocess.run too, so every probe of
    the binary would hit the fake as well; stub them out entirely."""
    burn._encoder_cache.clear()
    burn._version_cache.clear()
    burn._nvenc_test_cache.clear()
    monkeypatch.setattr(burn, "detect_nvenc", lambda: False)
    monkeypatch.setattr(burn, "_probe_best_effort", lambda *_a, **_k: None)
    monkeypatch.setattr(burn, "available_encoders", lambda _p: frozenset())
    monkeypatch.setattr(burn, "ffmpeg_major_version", lambda _p: None)
    monkeypatch.setattr(burn, "nvenc_encode_works", lambda _p: True)


@pytest.fixture
def inputs(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]", encoding="utf-8")
    return video, ass, tmp_path / "out" / "captioned.mp4"


class FakePopen:
    """Stands in for ffmpeg: emits the given progress lines, writes the
    output (the last argv element) as a real ffmpeg would, exits ``rc``."""

    instances: list["FakePopen"] = []

    def __init__(self, args, *, progress=(), rc=0, ffmpeg_stderr="", **kwargs):
        self.args = list(args)
        self.kwargs = kwargs  # what burn passed to Popen: cwd, pipes, creationflags
        self.stdout = iter(list(progress))
        self.stderr = iter([ffmpeg_stderr] if ffmpeg_stderr else [])
        self.rc = rc
        # Only a burn's argv ends in an output file; never write anywhere
        # else (a stray probe would otherwise drop a file in the cwd).
        assert self.args[-1].endswith(".part.mp4"), self.args
        Path(self.args[-1]).write_bytes(b"encoded")
        FakePopen.instances.append(self)

    def wait(self, timeout=None):
        return self.rc

    def poll(self):
        return self.rc

    def kill(self):
        pass


def _patch_popen(**fake_options):
    FakePopen.instances.clear()
    return patch(POPEN, side_effect=lambda args, **kw: FakePopen(args, **fake_options, **kw))


def test_missing_inputs_raise_without_launching(tmp_path):
    with patch(POPEN) as mock_popen:
        with pytest.raises(BurnInError, match="Input video not found"):
            burn_captions(tmp_path / "x.mp4", tmp_path / "x.ass", tmp_path / "o.mp4", duration_seconds=1.0)
        (tmp_path / "x.mp4").write_bytes(b"v")
        with pytest.raises(BurnInError, match="Subtitle file not found"):
            burn_captions(tmp_path / "x.mp4", tmp_path / "x.ass", tmp_path / "o.mp4", duration_seconds=1.0)
    mock_popen.assert_not_called()


def test_success_reports_progress_and_renames_the_part_file(inputs, tmp_path):
    video, ass, output = inputs
    progress = []
    with _patch_popen(progress=["out_time_us=2500000\n", "out_time_us=5000000\n"]):
        result = burn_captions(
            video, ass, output, duration_seconds=10.0, ffmpeg_path=tmp_path / "ffmpeg.exe",
            on_progress=progress.append,
        )
    assert result == output
    assert progress == [25.0, 50.0]
    assert output.read_bytes() == b"encoded"
    assert not part_path_for(output).exists()
    assert len(FakePopen.instances) == 1


def test_ffmpeg_runs_inside_the_work_directory_with_fixed_names(inputs, tmp_path):
    video, ass, output = inputs
    work = tmp_path / "work"
    with _patch_popen():
        burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "ffmpeg.exe", work_dir=work)
    fake = FakePopen.instances[0]
    assert Path(fake.kwargs["cwd"]) == work.resolve()
    assert fake.args[fake.args.index("-/filter:v") + 1] == "filter.txt"
    assert (work / "captions.ass").is_file() and (work / "filter.txt").is_file()
    assert fake.kwargs["stdin"] is subprocess.DEVNULL
    if sys.platform == "win32":
        assert fake.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_temporary_work_directory_is_removed_afterwards(inputs, tmp_path):
    video, ass, output = inputs
    with _patch_popen():
        burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "ffmpeg.exe")
    assert not Path(FakePopen.instances[0].kwargs["cwd"]).exists()


def test_failure_raises_with_stderr_and_removes_the_part_file(inputs, tmp_path):
    video, ass, output = inputs
    with _patch_popen(rc=1, ffmpeg_stderr="Unknown encoder 'h264_nvenc'"):
        with pytest.raises(BurnInError) as exc_info:
            burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "ffmpeg.exe")
    assert exc_info.value.returncode == 1
    assert "Unknown encoder" in exc_info.value.stderr
    assert not part_path_for(output).exists()
    assert not output.exists()


def test_launch_failure_is_a_burn_error(inputs, tmp_path):
    video, ass, output = inputs
    with patch(POPEN, side_effect=OSError("not found")):
        with pytest.raises(BurnInError, match="Failed to launch"):
            burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "missing.exe")


def test_video_info_drives_audio_and_bitrate(inputs, tmp_path, monkeypatch):
    video, ass, output = inputs
    monkeypatch.setattr(burn, "available_encoders", lambda _p: frozenset({"h264_mf"}))
    info = VideoInfo(3840, 2160, 30.0, 10.0, audio_codec="pcm_s16le")
    with _patch_popen():
        burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "ffmpeg.exe", video_info=info)
    args = FakePopen.instances[0].args
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:v") + 1] == str(burn.BITRATE_4K)


def test_probe_is_consulted_when_no_video_info_given(inputs, tmp_path, monkeypatch):
    video, ass, output = inputs
    seen = []
    monkeypatch.setattr(burn, "_probe_best_effort", lambda v, f: seen.append((v, f)) or None)
    with _patch_popen():
        burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "ffmpeg.exe")
    assert seen == [(video, tmp_path / "ffmpeg.exe")]


def test_nvenc_is_auto_detected(inputs, tmp_path, monkeypatch):
    video, ass, output = inputs
    monkeypatch.setattr(burn, "detect_nvenc", lambda: True)
    with _patch_popen():
        burn_captions(video, ass, output, duration_seconds=1.0, ffmpeg_path=tmp_path / "ffmpeg.exe")
    args = FakePopen.instances[0].args
    assert args[args.index("-c:v") + 1] == "h264_nvenc"


# ---------------------------------------------------------------------------
# live processes: cancellation and the kill registry
# ---------------------------------------------------------------------------

# A child that behaves like a slow ffmpeg: progress lines forever.
_SLOW_CHILD = [
    sys.executable, "-u", "-c",
    "import time\n"
    "for i in range(300):\n"
    "    print(f'out_time_us={(i + 1) * 100000}', flush=True)\n"
    "    time.sleep(0.1)\n",
]


def _popen_running_slow_child():
    real_popen = subprocess.Popen

    def fake(args, **kwargs):
        # keep every kwarg burn passes (cwd, pipes, creationflags) -- only
        # the program changes
        return real_popen(_SLOW_CHILD, **kwargs)

    return patch(POPEN, side_effect=fake)


def test_should_stop_kills_ffmpeg_removes_the_part_file_and_raises(inputs, tmp_path):
    video, ass, output = inputs
    output.parent.mkdir()
    part = part_path_for(output)
    part.write_bytes(b"half")  # what a real ffmpeg would have written so far
    polls = []

    def should_stop():
        polls.append(time.monotonic())
        return len(polls) >= 3

    started = time.monotonic()
    with _popen_running_slow_child():
        with pytest.raises(BurnCancelled):
            burn_captions(
                video, ass, output, duration_seconds=30.0, ffmpeg_path=tmp_path / "ffmpeg.exe",
                should_stop=should_stop,
            )
    assert time.monotonic() - started < 15
    assert len(polls) == 3
    assert not part.exists()
    assert not output.exists()
    assert active_processes() == []


def test_a_raising_progress_callback_still_kills_the_child(inputs, tmp_path):
    video, ass, output = inputs

    def explode(_pct):
        raise RuntimeError("ui gone")

    with _popen_running_slow_child():
        with pytest.raises(RuntimeError, match="ui gone"):
            burn_captions(
                video, ass, output, duration_seconds=30.0, ffmpeg_path=tmp_path / "ffmpeg.exe",
                on_progress=explode,
            )
    assert active_processes() == []
    assert not part_path_for(output).exists()


def test_kill_active_processes_stops_a_running_burn():
    """What tray Quit calls: the burn sees a non-zero exit, not a hang."""
    results = {}

    def run():
        results["run"] = run_ffmpeg(_SLOW_CHILD, duration_seconds=30.0)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not active_processes() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(active_processes()) == 1
    assert kill_active_processes() == 1
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert results["run"].returncode != 0
    assert results["run"].cancelled is False
    assert active_processes() == []


def test_run_ffmpeg_collects_stderr_and_returncode():
    child = [sys.executable, "-c", "import sys; print('out_time_us=500000'); sys.stderr.write('bad thing'); sys.exit(3)"]
    seen = []
    result = run_ffmpeg(child, duration_seconds=1.0, on_progress=seen.append)
    assert result.returncode == 3
    assert "bad thing" in result.stderr
    assert result.cancelled is False
    assert seen == [50.0]


def test_no_window_flags_matches_platform():
    flags = ffmpeg_process.no_window_flags()
    if sys.platform == "win32":
        assert flags == {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        assert flags == {}
