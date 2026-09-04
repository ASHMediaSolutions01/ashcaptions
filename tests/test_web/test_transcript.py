"""Caption check routes (routes_transcript.py): GET /api/jobs/{id}/transcript
reads the saved transcript beside the outputs; POST /api/jobs/{id}/translate
enqueues a translate-only job through the queue's optional submit_translate."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ash_captions.app.transcript import TranscriptRecord, save_transcript, transcript_path
from ash_captions.engine import Segment, Word
from ash_captions.web.models import JobOptions, JobStatus

from .conftest import LOCAL_BASE_URL
from .fakes import FakeJobQueue

SOURCE = (
    Word("hola", 0.0, 0.4, 0.95),
    Word("mundo", 0.4, 0.9, 0.42),
    Word("qué", 1.0, 1.2, 0.21),
    Word("tal", 1.2, 1.5, 0.88),
)
ENGLISH = (
    Word("hello", 0.0, 0.4),
    Word("world", 0.4, 0.9),
    Word("how", 1.0, 1.2),
    Word("are", 1.2, 1.4),
    Word("you", 1.4, 1.5),
)


@pytest.fixture
def fake_queue(tmp_path) -> FakeJobQueue:
    return FakeJobQueue(output_root=tmp_path / "out")


@pytest.fixture
def done_job(fake_queue, tmp_path):
    video = tmp_path / "footage" / "clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fake video")
    job = fake_queue.submit(video, JobOptions(language="es", dialect="es-MX", preset="POP"))
    fake_queue.force_status(job.id, JobStatus.DONE, progress=1.0)
    Path(job.output_dir).mkdir(parents=True)
    return fake_queue.get_job(job.id)


def _write_transcript(job, *, en_words=None, name="clip"):
    record = TranscriptRecord(
        language="es", dialect="es-MX", words=SOURCE,
        segments=(Segment("hola mundo qué tal", 0.0, 1.5, SOURCE),), en_words=en_words,
    )
    save_transcript(transcript_path(Path(job.output_dir), name), record)


class LegacyQueue:
    """A queue from before the caption check: reads only."""

    def __init__(self, inner: FakeJobQueue) -> None:
        self._inner = inner

    def list_jobs(self):
        return self._inner.list_jobs()

    def get_job(self, job_id):
        return self._inner.get_job(job_id)


class TestGetTranscript:
    def test_returns_words_with_confidence_and_no_english(self, client, done_job):
        _write_transcript(done_job)
        res = client.get(f"/api/jobs/{done_job.id}/transcript")
        assert res.status_code == 200
        body = res.json()
        assert body["language"] == "es"
        assert body["en_words"] is None
        assert body["words"][0] == {"w": "hola", "s": 0.0, "e": 0.4, "p": 0.95}
        assert [w["w"] for w in body["words"]] == ["hola", "mundo", "qué", "tal"]
        assert [w["p"] for w in body["words"]] == [0.95, 0.42, 0.21, 0.88]

    def test_english_words_carry_no_confidence(self, client, done_job):
        _write_transcript(done_job, en_words=ENGLISH)
        body = client.get(f"/api/jobs/{done_job.id}/transcript").json()
        assert body["en_words"][0] == {"w": "hello", "s": 0.0, "e": 0.4}
        assert [w["w"] for w in body["en_words"]] == ["hello", "world", "how", "are", "you"]

    def test_falls_back_to_any_transcript_in_the_folder(self, client, done_job):
        _write_transcript(done_job, name="renamed")
        assert client.get(f"/api/jobs/{done_job.id}/transcript").status_code == 200

    def test_404_without_a_saved_transcript(self, client, done_job):
        res = client.get(f"/api/jobs/{done_job.id}/transcript")
        assert res.status_code == 404
        assert "no saved transcript" in res.json()["detail"]

    def test_404_for_an_unknown_job(self, client):
        assert client.get("/api/jobs/nope/transcript").status_code == 404

    def test_unreadable_transcript_is_409(self, client, done_job):
        (Path(done_job.output_dir) / "clip.transcript.json").write_text("{not json", encoding="utf-8")
        res = client.get(f"/api/jobs/{done_job.id}/transcript")
        assert res.status_code == 409
        assert "clip.transcript.json" in res.json()["detail"]


class TestTranslate:
    def test_enqueues_a_pending_translate_job_for_the_same_footage(self, client, done_job, fake_queue):
        _write_transcript(done_job)
        res = client.post(f"/api/jobs/{done_job.id}/translate")
        assert res.status_code == 201
        created = res.json()
        assert created["id"] != done_job.id
        assert created["status"] == "pending"
        assert created["options"]["translate_to_english"] is True
        assert created["options"]["burn_in"] is False
        assert created["input_path"] == done_job.input_path
        assert created["output_dir"] == done_job.output_dir
        assert [j.id for j in fake_queue.translations] == [created["id"]]
        assert created["id"] in {j["id"] for j in client.get("/api/jobs").json()}

    def test_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/nope/translate").status_code == 404

    def test_job_without_a_transcript_is_409_with_the_queues_message(self, client, done_job, fake_queue):
        fake_queue.no_saved_words.add(done_job.id)
        res = client.post(f"/api/jobs/{done_job.id}/translate")
        assert res.status_code == 409
        assert "no saved transcript" in res.json()["detail"]

    def test_queue_without_submit_translate_is_501(self, client, done_job, fake_queue):
        client.app.state.queue = LegacyQueue(fake_queue)
        res = client.post(f"/api/jobs/{done_job.id}/translate")
        assert res.status_code == 501
        assert res.json()["detail"] == "this build cannot translate from Studio"

    def test_requires_the_client_header_like_every_mutation(self, app, done_job, fake_queue):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.post(f"/api/jobs/{done_job.id}/translate").status_code == 403
        assert fake_queue.translations == []
