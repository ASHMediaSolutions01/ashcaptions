"""probe_video honours the display rotation phones write: a 1920x1080
stream with rotation 90 decodes to 1080x1920 and that is the size captions
must be laid out for."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ash_captions.engine import probe


def _ffprobe_json(side_data=None, tags=None):
    video = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"}
    if side_data is not None:
        video["side_data_list"] = side_data
    if tags is not None:
        video["tags"] = tags
    return json.dumps({"streams": [video, {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "20.0"}})


def _probe(tmp_path: Path, payload: str):
    video = tmp_path / "phone.mp4"
    video.write_bytes(b"x")
    with patch("ash_captions.engine.probe.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
        info = probe.probe_video(video, ffprobe_path="ffprobe")
        args = run.call_args[0][0]
    return info, args


def test_display_matrix_rotation_swaps_the_frame_size(tmp_path):
    info, args = _probe(tmp_path, _ffprobe_json(side_data=[{"side_data_type": "Display Matrix", "rotation": -90}]))
    assert (info.width, info.height) == (1080, 1920)
    assert "stream_side_data=rotation" in " ".join(args)


def test_legacy_rotate_tag_swaps_too(tmp_path):
    info, _ = _probe(tmp_path, _ffprobe_json(tags={"rotate": "270"}))
    assert (info.width, info.height) == (1080, 1920)


@pytest.mark.parametrize("rotation", [0, 180, -180, "garbage", None])
def test_no_or_half_turn_rotation_keeps_the_size(tmp_path, rotation):
    side = [{"rotation": rotation}] if rotation is not None else []
    info, _ = _probe(tmp_path, _ffprobe_json(side_data=side))
    assert (info.width, info.height) == (1920, 1080)
