"""Opt-in tests against a real ffmpeg binary.

Skipped unless ``ASH_REAL_FFMPEG`` points at an ffmpeg executable (ffprobe
is expected beside it), e.g.::

    ASH_REAL_FFMPEG="C:\\path\\to\\bin\\ffmpeg.exe" pytest tests/test_engine/test_real_ffmpeg.py

The mocked suite stayed green while real burns were broken four times;
these run the actual binary on a synthetic clip.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ash_captions.engine.burn import burn_captions, part_path_for
from ash_captions.engine.probe import ffprobe_beside, probe_video
from ash_captions.engine.punch import PunchMoment, build_punch_filter
from ash_captions.engine.rules import build_cards
from ash_captions.engine.transcribe import Word
from ash_captions.engine.writers import CLEAN, write_ass

FFMPEG = os.environ.get("ASH_REAL_FFMPEG", "")

pytestmark = pytest.mark.skipif(
    not (FFMPEG and Path(FFMPEG).is_file()), reason="set ASH_REAL_FFMPEG to an ffmpeg binary to run"
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """A 3s 1080x1920 testsrc2 clip with a sine tone, in a folder whose
    name would break a filtergraph."""
    folder = tmp_path_factory.mktemp("real") / "Client's folder, take 2"
    folder.mkdir()
    path = folder / "Client's reel (v2).mp4"
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def captions(clip) -> Path:
    words = [Word("Hello", 0.2, 0.6), Word("client's", 0.6, 1.2), Word("reel.", 1.2, 1.8), Word("Ready?", 2.0, 2.8)]
    ass = clip.parent / "it's the captions.ass"
    write_ass(build_cards(words, max_words=2, min_words=1, silence_gap=1.0), ass, CLEAN)
    return ass


def _streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [str(ffprobe_beside(FFMPEG)), "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["streams"]


def _assert_playable_yuv420p(output: Path) -> None:
    assert output.is_file()
    assert not part_path_for(output).exists()
    streams = _streams(output)
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert int(video.get("nb_frames", 0)) >= 80
    assert any(s["codec_type"] == "audio" for s in streams)


def test_burns_a_clip_whose_path_has_an_apostrophe(clip, captions):
    output = clip.parent / "Client's reel (v2).captioned.mp4"
    progress: list[float] = []
    info = probe_video(clip, ffprobe_path=ffprobe_beside(FFMPEG))
    result = burn_captions(
        clip, captions, output, duration_seconds=info.duration_seconds, ffmpeg_path=FFMPEG, use_nvenc=False,
        video_info=info, on_progress=progress.append,
    )
    assert result == output
    _assert_playable_yuv420p(output)
    assert progress and progress[-1] > 50


def test_burns_with_900_punch_moments(clip, captions):
    """~80 characters per moment: far past the 32,767-character command line
    limit that made a 60-minute talk fail to launch ffmpeg at all."""
    moments = [PunchMoment(i * 0.003, i * 0.003 + 0.002, "sentence") for i in range(900)]
    punch = build_punch_filter(moments, zoom=1.12)
    assert len(punch) > 60_000
    output = clip.parent / "punched.captioned.mp4"
    burn_captions(
        clip, captions, output, duration_seconds=3.0, ffmpeg_path=FFMPEG, use_nvenc=False, punch_filter=punch,
    )
    _assert_playable_yuv420p(output)
