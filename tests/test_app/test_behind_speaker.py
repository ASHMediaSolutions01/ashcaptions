"""'Captions behind the speaker' through the runner: a matte pass before the
burn, the matte handed to burn_captions, and plain failures when the effect
cannot be produced."""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions import engine
from ash_captions.app.runner import build_run_job
from ash_captions.pipeline.db import STAGES, JobOptions, JobStore

from .test_studio_pipeline import CountingTranscriber, _job, _settings, _video


class _Reporter:
    def __init__(self):
        self.stages: list[str] = []
        self.progress: list[int] = []

    def __call__(self, p):
        self.progress.append(p)

    def stage(self, name):
        self.stages.append(name)

    def should_stop(self):
        return False


@pytest.fixture(autouse=True)
def fake_media(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {"matte": [], "burn": []}

    def fake_extract(video_path, output_path, *, ffmpeg_path=None):
        Path(output_path).write_bytes(b"RIFF")
        return Path(output_path)

    def fake_probe(video_path, *, ffprobe_path=None):
        return engine.VideoInfo(1080, 1920, 30.0, 20.0)

    def fake_ensure(models_dir, *, download=True, timeout=120):
        return Path(models_dir) / "rvm.onnx"

    def fake_matte(video_path, matte_path, *, model_path, width, height, fps, duration_seconds, **kw):
        Path(matte_path).parent.mkdir(parents=True, exist_ok=True)
        Path(matte_path).write_bytes(b"matte")
        calls["matte"].append(dict(width=width, height=height, fps=fps))
        if kw.get("on_progress"):
            kw["on_progress"](50.0)
            kw["on_progress"](100.0)
        return engine.MatteResult(Path(matte_path), 480, 854, fps, 600)

    def fake_burn(video_path, ass_path, output_path, *, duration_seconds, **kw):
        calls["burn"].append(kw)
        Path(output_path).write_bytes(b"mp4")
        return Path(output_path)

    monkeypatch.setattr(engine, "extract_audio", fake_extract)
    monkeypatch.setattr(engine, "probe_video", fake_probe)
    monkeypatch.setattr(engine, "ensure_matte_model", fake_ensure)
    monkeypatch.setattr(engine, "render_matte", fake_matte)
    monkeypatch.setattr(engine, "burn_captions", fake_burn)
    return calls


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def test_matte_stage_exists():
    assert "matte" in STAGES


def test_behind_speaker_mattes_then_burns_with_the_matte(tmp_path, store, fake_media):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    job = _job(store, video, tmp_path / "out" / "clip", burn=True, behind_speaker=True)
    rep = _Reporter()
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(job, rep)

    assert fake_media["matte"] == [dict(width=1080, height=1920, fps=30.0)]
    assert len(fake_media["burn"]) == 1
    assert Path(fake_media["burn"][0]["matte_path"]).name == "matte.mp4"
    assert rep.stages[-2:] == ["matte", "burn"]
    assert (tmp_path / "out" / "clip" / "clip.captioned.mp4").is_file()
    assert rep.progress == sorted(rep.progress), "progress must stay monotonic across matte and burn"


def test_plain_burn_does_not_matte(tmp_path, store, fake_media):
    settings = _settings(tmp_path)
    job = _job(store, _video(tmp_path), tmp_path / "out" / "clip", burn=True)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(job, _Reporter())
    assert fake_media["matte"] == []
    assert fake_media["burn"][0].get("matte_path") is None


def test_missing_model_fails_the_job_plainly(tmp_path, store, fake_media, monkeypatch):
    def refuse(models_dir, *, download=True, timeout=120):
        raise engine.MatteError("The person-matting model is not installed")

    monkeypatch.setattr(engine, "ensure_matte_model", refuse)
    settings = _settings(tmp_path)
    job = _job(store, _video(tmp_path), tmp_path / "out" / "clip", burn=True, behind_speaker=True)
    with pytest.raises(engine.MatteError, match="not installed"):
        build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(job, _Reporter())
    assert fake_media["burn"] == []


def test_job_options_round_trip_the_flag():
    opts = JobOptions("en", None, "POP", True, False, behind_speaker=True)
    assert JobOptions.from_json(opts.to_json()).behind_speaker is True
    assert JobOptions.from_json('{"language": "en", "preset": "POP"}').behind_speaker is False
