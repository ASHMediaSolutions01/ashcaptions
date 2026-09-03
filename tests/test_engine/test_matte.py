"""Person matting: model handling, the frame loop with a fake model, the
compositing graph, and the burn command with a matte."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ash_captions.engine import encoders, matte
from ash_captions.engine.burn import build_burn_command


def test_working_size_caps_the_short_side_and_keeps_even_dimensions():
    assert matte.working_size(1080, 1920) == (480, 854)
    assert matte.working_size(1920, 1080) == (854, 480)
    assert matte.working_size(320, 240) == (320, 240)  # never upscaled
    assert matte.working_size(0, 0) == (480, 480)


def test_ensure_matte_model_refuses_plainly_without_download(tmp_path):
    with pytest.raises(matte.MatteError, match="not installed"):
        matte.ensure_matte_model(tmp_path, download=False)


def test_ensure_matte_model_returns_an_existing_file(tmp_path):
    path = matte.matte_model_path(tmp_path)
    path.write_bytes(b"x" * (matte.MATTE_MODEL_MIN_BYTES + 1))
    assert matte.ensure_matte_model(tmp_path, download=False) == path


def test_composite_graph_shape():
    graph = matte.composite_filtergraph(caption_filter="ass=captions.ass", width=1080, height=1920, fps=30)
    assert graph.startswith("[0:v]fps=30[base];[base]split[b1][b2];[b1]ass=captions.ass[cap];")
    assert "[1:v]fps=30,scale=1080:1920" in graph and "format=gray[al]" in graph
    assert "[b2][al]alphamerge[fg];[cap][fg]overlay=0:0:format=auto,format=yuv420p[out]" in graph


def test_composite_graph_punches_both_branches():
    graph = matte.composite_filtergraph(caption_filter="ass=c.ass", width=100, height=100, fps=25, punch_filter="scale=iw:ih")
    assert graph.count("scale=iw:ih") == 2


class _FakeSession:
    def __init__(self):
        self.frames = 0

    def alpha(self, rgb, w, h):
        self.frames += 1
        return bytes([255]) * (w * h)


REAL_FFMPEG = os.environ.get("ASH_REAL_FFMPEG")


@pytest.mark.skipif(not REAL_FFMPEG, reason="needs the real ffmpeg (ASH_REAL_FFMPEG=<path>)")
def test_render_matte_with_a_fake_model_produces_a_playable_clip(tmp_path):
    ffmpeg = REAL_FFMPEG if REAL_FFMPEG not in ("1", "true") else "ffmpeg"
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=10:duration=2", "-pix_fmt", "yuv420p", str(src)], check=True)
    fake = _FakeSession()
    progress = []
    result = matte.render_matte(
        src, tmp_path / "matte.mp4", model_path=tmp_path / "unused.onnx", width=320, height=240, fps=10,
        duration_seconds=2.0, ffmpeg_path=ffmpeg, session_factory=lambda _p: fake, on_progress=progress.append,
    )
    assert result.frames == 20 and fake.frames == 20
    assert result.path.is_file() and result.path.stat().st_size > 0
    assert progress and progress[-1] == 100.0


def test_burn_command_with_matte_uses_a_complex_graph(tmp_path, monkeypatch):
    from ash_captions.engine import burn as burn_mod

    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"libx264"}))
    monkeypatch.setattr(encoders, "ffmpeg_major_version", lambda _p: 8)
    ass = tmp_path / "c.ass"
    ass.write_text("[Script Info]", encoding="utf-8")
    matte_file = tmp_path / "m.mp4"
    matte_file.write_bytes(b"m")
    work = tmp_path / "work"
    args = build_burn_command(
        tmp_path / "v.mp4", ass, tmp_path / "out.mp4", work_dir=work, ffmpeg_path=tmp_path / "ffmpeg.exe",
        width=1080, height=1920, fps=30, matte_path=matte_file,
    )
    assert args.count("-i") == 2 and str(matte_file) in args
    assert "[out]" in args and "-/filter_complex" in args
    graph = (work / "filter.txt").read_text(encoding="utf-8")
    assert "alphamerge" in graph and "ass=captions.ass" in graph


def test_burn_command_with_matte_needs_the_frame_size(tmp_path, monkeypatch):
    from ash_captions.engine import burn as burn_mod

    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"libx264"}))
    ass = tmp_path / "c.ass"
    ass.write_text("[Script Info]", encoding="utf-8")
    with pytest.raises(burn_mod.BurnInError, match="frame size"):
        build_burn_command(tmp_path / "v.mp4", ass, tmp_path / "o.mp4", work_dir=tmp_path / "w",
                           ffmpeg_path=tmp_path / "ffmpeg.exe", matte_path=tmp_path / "m.mp4")
