"""Track B (v0.5 caption check), pipeline side: the translate-only job mode
adds English to a saved transcript without transcribing again, and the
queue adapter can enqueue one. Helpers mirror test_studio_pipeline.py so
this file reads on its own."""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions import engine
from ash_captions.app import adapter as adapter_module
from ash_captions.app import runner as runner_module
from ash_captions.app.runner import build_run_job
from ash_captions.app.runner_util import _CANCEL_EXCEPTIONS, _progress_budget
from ash_captions.app.transcript import load_transcript, transcript_path
from ash_captions.config import Settings
from ash_captions.engine.transcribe import TranscriptionCancelled
from ash_captions.pipeline.db import JobOptions, JobStore
from ash_captions.pipeline.queue import JobCancelled, ProgressReporter


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        in_dir=tmp_path / "in", out_dir=tmp_path / "out", db_path=tmp_path / "jobs.db",
        log_path=tmp_path / "log.txt", glossary_dir=tmp_path / "glossaries",
        upload_dir=tmp_path / "uploads", tmp_dir=tmp_path / "tmp", min_free_disk_gb=0,
    )


def _result(texts: list[str], *, step: float = 0.5):
    words, t = [], 0.0
    for text in texts:
        words.append(engine.Word(text=text, start=t, end=t + step))
        t += step
    seg = engine.Segment(text=" ".join(texts), start=0.0, end=t, words=tuple(words))
    return engine.TranscriptionResult(segments=(seg,), language="es")


class RecordingTranscriber:
    """Records which passes ran (and their prompts); answers fixed results."""

    def __init__(self):
        self.transcribe_result = _result(["hola", "amigo", "cómo", "estás", "hoy", "bien"])
        self.translate_result = _result(["hello", "there", "friend", "how", "are", "you"])
        self.calls: list[str] = []
        self.prompts: dict[str, object] = {}

    def transcribe(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
        self.calls.append("transcribe")
        self.prompts["transcribe"] = initial_prompt
        return self.transcribe_result

    def translate(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
        self.calls.append("translate")
        self.prompts["translate"] = initial_prompt
        return self.translate_result


@pytest.fixture(autouse=True)
def no_real_media(monkeypatch: pytest.MonkeyPatch):
    def fake_extract(video_path, output_path, *, ffmpeg_path=None):
        Path(output_path).write_bytes(b"RIFF")
        return Path(output_path)

    def fake_burn(video_path, ass_path, output_path, *, duration_seconds, **_):
        Path(output_path).write_bytes(b"fake mp4")
        return Path(output_path)

    monkeypatch.setattr(engine, "extract_audio", fake_extract)
    monkeypatch.setattr(engine, "burn_captions", fake_burn)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def _video(tmp_path: Path) -> Path:
    video = tmp_path / "footage" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake video")
    return video


def _job(store: JobStore, video: Path, out: Path, **overrides):
    fields = dict(language="en", dialect=None, preset="POP", burn=False, translate=False)
    fields.update(overrides)
    return store.get_job(store.insert_job(video, out, JobOptions(**fields)))


class _Reporter:
    def __call__(self, p): pass
    def stage(self, name): pass
    def should_stop(self): return False


def _spanish_job_done(tmp_path: Path, store: JobStore):
    """A finished Spanish job with its transcript saved and no English."""
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    out = tmp_path / "out" / "clip"
    job = _job(store, video, out, language="es", dialect="es-MX")
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=RecordingTranscriber())(job, _Reporter())
    store.mark_done(job.id)
    assert load_transcript(transcript_path(out, "clip")).en_words is None
    return settings, video, out


# -- progress budget ------------------------------------------------------------


def test_translate_only_budget_skips_transcription_and_spans_the_bar():
    budget = _progress_budget(translate=True, burn=False, transcribe=False)
    assert list(budget) == ["extract", "translate", "postprocess", "cards_and_write"]
    assert budget["extract"][0] == 0 and budget["cards_and_write"][1] == 100
    spans = {name: end - start for name, (start, end) in budget.items()}
    assert spans["translate"] == max(spans.values())


def test_default_budget_is_unchanged_by_the_new_keyword():
    assert _progress_budget(translate=True, burn=True) == _progress_budget(translate=True, burn=True, transcribe=True)
    assert "transcribe" in _progress_budget(translate=False, burn=False)


def test_cancel_exceptions_live_in_runner_util_and_stay_reachable_from_runner():
    assert runner_module._CANCEL_EXCEPTIONS is _CANCEL_EXCEPTIONS
    assert TranscriptionCancelled in _CANCEL_EXCEPTIONS
