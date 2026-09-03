"""Per-client glossaries through the pipeline: the runner applies a
client's entries (winning over the shared file) to the words, the
segments and the English translation; the queue adapter names a
watch-folder drop's client from its subfolder; and the consumed-input
rule still holds for a nested drop. Real JobStore, real runner, fake
transcriber (as in test_studio_pipeline.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions import engine
from ash_captions.app.adapter import QueueAdapter
from ash_captions.app.runner import _is_within, build_run_job
from ash_captions.app.runner_util import client_for_watch_path
from ash_captions.config import Settings
from ash_captions.pipeline.db import JobOptions, JobStore
from ash_captions.web.models import JobOptions as WebJobOptions


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
    return engine.TranscriptionResult(segments=(seg,), language="en")


class FakeTranscriber:
    def __init__(self, texts):
        self.result = _result(texts)

    def transcribe(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
        return self.result

    translate = transcribe


class _Reporter:
    def __call__(self, p): pass
    def stage(self, name): pass
    def should_stop(self): return False


@pytest.fixture(autouse=True)
def no_real_media(monkeypatch: pytest.MonkeyPatch):
    def fake_extract(video_path, output_path, *, ffmpeg_path=None):
        Path(output_path).write_bytes(b"RIFF")
        return Path(output_path)

    monkeypatch.setattr(engine, "extract_audio", fake_extract)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def _video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake video")
    return path


def _job(store: JobStore, video: Path, out: Path, **overrides):
    fields = dict(language="en", dialect=None, preset="POP", burn=False, translate=False)
    fields.update(overrides)
    return store.get_job(store.insert_job(video, out, JobOptions(**fields)))


def _write_glossaries(settings: Settings) -> None:
    settings.glossary_dir.mkdir(parents=True, exist_ok=True)
    (settings.glossary_dir / "glossary.txt").write_text("gazi => Ghazi\nbroker => Agent\n", encoding="utf-8")
    (settings.glossary_dir / "acme.txt").write_text("broker => Brokerage\nwidget => Acme Widget\n", encoding="utf-8")


# -- runner ----------------------------------------------------------------------


def test_client_entries_win_over_shared_in_words_segments_and_english(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    _write_glossaries(settings)
    video = _video(tmp_path / "footage" / "clip.mp4")
    out = tmp_path / "out" / "clip"
    job = _job(store, video, out, client="Acme", translate=True)
    transcriber = FakeTranscriber(["the", "broker", "met", "gazi", "about", "a", "widget"])

    build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)(job, _Reporter())

    srt = (out / "clip.srt").read_text(encoding="utf-8")
    assert "Brokerage" in srt and "Agent" not in srt  # the client's spelling, not the shared one
    assert "Ghazi" in srt  # a shared entry the client didn't override still applies
    assert "Acme Widget" in srt
    txt = (out / "clip.txt").read_text(encoding="utf-8")
    assert "Brokerage" in txt and "Ghazi" in txt and "Acme Widget" in txt
    en = (out / "clip.en.srt").read_text(encoding="utf-8")
    assert "Brokerage" in en and "Ghazi" in en


def test_a_job_without_a_client_gets_the_shared_glossary_only(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    _write_glossaries(settings)
    video = _video(tmp_path / "footage" / "clip.mp4")
    out = tmp_path / "out" / "clip"
    job = _job(store, video, out)

    build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(["the", "broker", "widget"]))(job, _Reporter())

    srt = (out / "clip.srt").read_text(encoding="utf-8")
    assert "Agent" in srt and "Brokerage" not in srt and "widget" in srt


def test_a_client_without_a_file_is_the_shared_glossary(tmp_path: Path, store: JobStore, caplog):
    settings = _settings(tmp_path)
    _write_glossaries(settings)
    video = _video(tmp_path / "footage" / "clip.mp4")
    out = tmp_path / "out" / "clip"
    job = _job(store, video, out, client="Globex")

    with caplog.at_level("INFO", logger="ash_captions.languages.glossary"):
        build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(["broker"]))(job, _Reporter())

    assert "Agent" in (out / "clip.srt").read_text(encoding="utf-8")
    assert "globex.txt" in caplog.text and "glossary.txt" in caplog.text  # both loads are logged


# -- watch folder: in\<Client>\clip.mp4 ------------------------------------------


def test_client_for_watch_path(tmp_path: Path):
    watch = tmp_path / "in"
    assert client_for_watch_path(watch / "Acme" / "clip.mp4", watch) == "Acme"
    assert client_for_watch_path(watch / "Acme Corp" / "clip.mp4", watch) == "Acme Corp"
    assert client_for_watch_path(watch / "clip.mp4", watch) is None
    assert client_for_watch_path(tmp_path / "elsewhere" / "Acme" / "clip.mp4", watch) is None
    assert client_for_watch_path(watch / "CON" / "clip.mp4", watch) is None  # unusable folder name
    assert client_for_watch_path(watch / "Acme" / "sub" / "clip.mp4", watch) == "Acme"


def test_adapter_names_the_client_from_the_watch_subfolder(tmp_path: Path, store: JobStore):
    watch = tmp_path / "in"
    video = _video(watch / "Acme" / "clip.mp4")
    adapter = QueueAdapter(store, out_dir=tmp_path / "out", watch_dir=watch)
    defaults = WebJobOptions(language="en", dialect=None, preset="POP")

    job = adapter.submit(video, defaults)

    assert job.options.client == "Acme"
    assert store.get_job(int(job.id)).options.client == "Acme"
    assert adapter.known_clients() == ["Acme"]


def test_adapter_keeps_an_explicit_client_over_the_folder(tmp_path: Path, store: JobStore):
    watch = tmp_path / "in"
    video = _video(watch / "Acme" / "clip.mp4")
    adapter = QueueAdapter(store, out_dir=tmp_path / "out", watch_dir=watch)

    job = adapter.submit(video, WebJobOptions(language="en", dialect=None, preset="POP", client="Globex"))

    assert job.options.client == "Globex"


def test_adapter_without_watch_dir_reads_it_from_settings(tmp_path: Path, store: JobStore):
    """`app/__main__.py` builds the adapter without `watch_dir`; the folder
    rule must still hold from the loaded settings."""
    watch = tmp_path / "in"
    video = _video(watch / "Acme" / "clip.mp4")
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)

    job = adapter.submit(video, WebJobOptions(language="en", dialect=None, preset="POP"))

    assert job.options.client == "Acme"


def test_nested_watch_drop_is_still_consumed_after_success(tmp_path: Path, store: JobStore):
    """The delete-consumed-input rule uses `_is_within`, which covers a
    nested path; a file in in\\Acme\\ is ours to delete, one elsewhere never."""
    settings = _settings(tmp_path)
    nested = _video(settings.in_dir / "Acme" / "clip.mp4")
    outside = _video(tmp_path / "footage" / "clip.mp4")
    assert _is_within(nested, settings.in_dir) and not _is_within(outside, settings.in_dir)

    run = build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(["hi"]))
    after = run(_job(store, nested, tmp_path / "out" / "clip", client="Acme"), _Reporter())
    assert after is not None
    after()
    assert not nested.exists()

    assert run(_job(store, outside, tmp_path / "out" / "clip2"), _Reporter()) is None
    assert outside.exists()
