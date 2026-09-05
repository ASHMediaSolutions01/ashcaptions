"""Editing the transcript through the API (v0.6 section 1):
`PATCH /api/jobs/{id}/transcript` applies a list of operations atomically,
re-renders the caption files from the result and guards the whole thing
with one revision counter; `POST /api/jobs/{id}/glossary` writes the
correction where the *next* job will read it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ash_captions.app.transcript import TranscriptRecord, load_transcript, save_transcript, transcript_path
from ash_captions.engine import Segment, Word
from ash_captions.web.models import JobOptions, JobStatus

from .conftest import LOCAL_BASE_URL
from .fakes import FakeJobQueue
from .fakes_providers import FakeGlossaryProvider

SOURCE = (
    Word("Coge", 0.0, 0.40, 0.95),
    Word("la", 0.40, 0.55, 0.99),
    Word("haramienta", 0.55, 1.10, 0.41),
    Word("grande", 1.10, 1.60, 0.97),
    Word("de", 1.60, 1.75, 0.98),
    Word("la", 1.75, 1.90, 0.98),
    Word("haramienta", 1.90, 2.40, 0.44),
)


class GlossaryFilesFake(FakeGlossaryProvider):
    """The in-memory provider plus the one attribute the real one has and
    the shared-glossary path needs."""

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.glossary_dir = directory


@pytest.fixture
def fake_queue(tmp_path) -> FakeJobQueue:
    return FakeJobQueue(output_root=tmp_path / "out")


@pytest.fixture
def fake_glossary_provider(tmp_path) -> GlossaryFilesFake:
    directory = tmp_path / "glossary"
    directory.mkdir()
    return GlossaryFilesFake(directory)


def _job(fake_queue, tmp_path, *, client=None):
    video = tmp_path / "footage" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake video")
    job = fake_queue.submit(video, JobOptions(language="es", preset="CLEAN", client=client))
    fake_queue.force_status(job.id, JobStatus.DONE, progress=1.0)
    Path(job.output_dir).mkdir(parents=True)
    return fake_queue.get_job(job.id)


@pytest.fixture
def done_job(fake_queue, tmp_path):
    return _job(fake_queue, tmp_path)


def _write_transcript(job, words=SOURCE):
    record = TranscriptRecord(
        language="es",
        words=words,
        segments=(Segment(" ".join(w.text for w in words), words[0].start, words[-1].end, words),),
    )
    return save_transcript(transcript_path(Path(job.output_dir), "clip"), record)


def _patch(client, job, revision, *ops):
    return client.patch(
        f"/api/jobs/{job.id}/transcript", json={"revision": revision, "ops": list(ops)}
    )


class TestGetGrowsTheEditingState:
    def test_an_unedited_transcript_reports_revision_zero_and_no_meta(self, client, done_job):
        _write_transcript(done_job)
        body = client.get(f"/api/jobs/{done_job.id}/transcript").json()
        assert body["revision"] == 0
        assert body["meta"] is None
        assert body["en_stale"] is False
        assert body["words"][0] == {"w": "Coge", "s": 0.0, "e": 0.4, "p": 0.95}

    def test_after_an_edit_meta_is_as_long_as_the_words(self, client, done_job):
        _write_transcript(done_job)
        _patch(client, done_job, 0, {"op": "set_text", "index": 2, "text": "herramienta"})
        body = client.get(f"/api/jobs/{done_job.id}/transcript").json()
        assert body["revision"] == 1
        assert len(body["meta"]) == len(body["words"])
        assert body["meta"][2]["edited"] is True
        assert body["en_stale"] is True


class TestPatchOperations:
    def test_set_text_answers_the_whole_new_transcript_and_the_card_count(self, client, done_job):
        _write_transcript(done_job)
        res = _patch(client, done_job, 0, {"op": "set_text", "index": 2, "text": "herramienta"})
        assert res.status_code == 200
        body = res.json()
        assert body["revision"] == 1
        assert body["cards"] >= 1
        assert body["words"][2] == {"w": "herramienta", "s": 0.55, "e": 1.1, "p": 0.0}
        assert body["meta"][2]["edited"] is True

    def test_a_bulk_fix_changes_every_occurrence(self, client, done_job):
        _write_transcript(done_job)
        body = _patch(
            client, done_job, 0, {"op": "set_text", "index": 2, "text": "herramienta", "all": True}
        ).json()
        assert [w["w"] for w in body["words"]].count("herramienta") == 2

    def test_retime_is_clamped_by_the_neighbours(self, client, done_job):
        _write_transcript(done_job)
        body = _patch(client, done_job, 0, {"op": "retime", "index": 3, "start": 0.0, "end": 9.0}).json()
        assert (body["words"][3]["s"], body["words"][3]["e"]) == (1.1, 1.6)
        assert body["meta"][3]["retimed"] is True

    def test_split_and_merge_move_the_line_break_in_the_srt(self, client, done_job):
        _write_transcript(done_job)
        srt = Path(done_job.output_dir) / "clip.srt"
        _patch(client, done_job, 0, {"op": "split", "index": 1})
        assert srt.read_text(encoding="utf-8").splitlines()[2] == "Coge"
        _patch(client, done_job, 1, {"op": "merge", "index": 1})
        assert srt.read_text(encoding="utf-8").splitlines()[2].startswith("Coge la")

    def test_set_style_is_stored_and_read_back(self, client, done_job):
        _write_transcript(done_job)
        body = _patch(
            client, done_job, 0, {"op": "set_style", "index": 2, "style": {"colour": "#FFD166", "scale": 1.25}}
        ).json()
        assert body["meta"][2]["style"] == {"colour": "#FFD166", "scale": 1.25}
        body = _patch(client, done_job, 1, {"op": "set_style", "index": 2, "style": None}).json()
        assert body["meta"] is None

    def test_several_ops_apply_in_order_as_one_revision_step_each(self, client, done_job):
        _write_transcript(done_job)
        body = _patch(
            client,
            done_job,
            0,
            {"op": "set_text", "index": 2, "text": "herramienta"},
            {"op": "split", "index": 2},
            {"op": "set_style", "index": 2, "style": {"bold": True}},
        ).json()
        assert body["revision"] == 3
        assert body["meta"][2] == {
            "edited": True, "retimed": False, "break_before": True, "no_break_before": False,
            "style": {"bold": True},
        }

    def test_the_caption_files_on_disk_all_carry_the_edit(self, client, done_job):
        _write_transcript(done_job)
        _patch(client, done_job, 0, {"op": "set_text", "index": 2, "text": "herramienta", "all": True})
        out = Path(done_job.output_dir)
        for name in ("clip.srt", "clip.ass", "clip.txt"):
            text = (out / name).read_text(encoding="utf-8")
            assert "herramienta" in text, name
            assert "haramienta" not in text, name

    def test_nothing_is_written_when_an_op_in_the_middle_is_bad(self, client, done_job):
        path = _write_transcript(done_job)
        before = path.read_text(encoding="utf-8")
        res = _patch(
            client,
            done_job,
            0,
            {"op": "set_text", "index": 2, "text": "herramienta"},
            {"op": "split", "index": 99},
        )
        assert res.status_code == 409
        assert "no word 99" in res.json()["detail"]
        assert path.read_text(encoding="utf-8") == before
        assert not (Path(done_job.output_dir) / "clip.srt").exists()

    @pytest.mark.parametrize(
        "op,detail",
        [
            ({"op": "set_text", "index": 0, "text": "   "}, "empty"),
            ({"op": "set_text", "index": 0}, "needs the new text"),
            ({"op": "retime", "index": 0}, "start, an end"),
            ({"op": "split", "index": 0}, "first word"),
            ({"op": "set_style", "index": 0, "style": {"scale": 9}}, "style.scale"),
            ({"op": "set_style", "index": 0, "style": {"font": "Arial"}}, "unknown field"),
        ],
    )
    def test_a_bad_value_is_a_409_saying_which(self, client, done_job, op, detail):
        _write_transcript(done_job)
        res = _patch(client, done_job, 0, op)
        assert res.status_code == 409
        assert detail in res.json()["detail"]

    @pytest.mark.parametrize(
        "body",
        [
            {"revision": 0, "ops": []},
            {"revision": 0, "ops": [{"op": "rename", "index": 0}]},
            {"revision": 0, "ops": [{"op": "split", "index": -1}]},
            {"ops": [{"op": "split", "index": 1}]},
        ],
    )
    def test_a_body_that_is_not_a_patch_is_refused_by_the_model(self, client, done_job, body):
        _write_transcript(done_job)
        assert client.patch(f"/api/jobs/{done_job.id}/transcript", json=body).status_code == 422


class TestRevisionGuard:
    def test_a_stale_revision_is_409_carrying_the_current_transcript(self, client, done_job):
        _write_transcript(done_job)
        _patch(client, done_job, 0, {"op": "set_text", "index": 2, "text": "herramienta"})
        res = _patch(client, done_job, 0, {"op": "set_text", "index": 2, "text": "otra"})
        assert res.status_code == 409
        body = res.json()
        assert "changed this transcript" in body["detail"]
        assert body["transcript"]["revision"] == 1
        assert body["transcript"]["words"][2]["w"] == "herramienta"

    def test_the_second_tab_can_carry_on_from_the_revision_it_was_given(self, client, done_job):
        _write_transcript(done_job)
        _patch(client, done_job, 0, {"op": "split", "index": 3})
        current = _patch(client, done_job, 0, {"op": "split", "index": 4}).json()["transcript"]["revision"]
        assert _patch(client, done_job, current, {"op": "split", "index": 4}).status_code == 200


class TestPatchGuards:
    def test_an_unknown_job_is_404(self, client):
        assert client.patch("/api/jobs/nope/transcript", json={"revision": 0, "ops": [{"op": "split", "index": 1}]}).status_code == 404

    def test_a_job_with_no_transcript_is_404(self, client, done_job):
        assert _patch(client, done_job, 0, {"op": "split", "index": 1}).status_code == 404

    def test_an_unreadable_transcript_is_409(self, client, done_job):
        (Path(done_job.output_dir) / "clip.transcript.json").write_text("{not json", encoding="utf-8")
        res = _patch(client, done_job, 0, {"op": "split", "index": 1})
        assert res.status_code == 409
        assert "clip.transcript.json" in res.json()["detail"]

    def test_it_needs_the_client_header_like_every_mutation(self, app, done_job):
        _write_transcript(done_job)
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        res = foreign.patch(
            f"/api/jobs/{done_job.id}/transcript",
            json={"revision": 0, "ops": [{"op": "split", "index": 1}]},
        )
        assert res.status_code == 403
        assert load_transcript(Path(done_job.output_dir) / "clip.transcript.json").revision == 0


class TestGlossaryFromTheStudio:
    def test_a_client_job_writes_to_that_clients_file(self, client, fake_queue, tmp_path, fake_glossary_provider):
        job = _job(fake_queue, tmp_path, client="Acme Corp")
        res = client.post(f"/api/jobs/{job.id}/glossary", json={"from": "haramienta", "to": "herramienta"})
        assert res.status_code == 201
        assert res.json() == {"client": "Acme Corp", "line": "haramienta => herramienta", "added": True}
        assert fake_glossary_provider.files["acme-corp"] == "haramienta => herramienta\n"

    def test_a_job_with_no_client_writes_to_the_shared_file(self, client, done_job, fake_glossary_provider):
        res = client.post(f"/api/jobs/{done_job.id}/glossary", json={"from": "haramienta", "to": "herramienta"})
        assert res.status_code == 201
        assert res.json()["client"] is None
        shared = fake_glossary_provider.glossary_dir / "glossary.txt"
        assert shared.read_text(encoding="utf-8") == "haramienta => herramienta\n"

    def test_a_second_line_is_appended_below_the_first(self, client, done_job, fake_glossary_provider):
        client.post(f"/api/jobs/{done_job.id}/glossary", json={"from": "a", "to": "b"})
        client.post(f"/api/jobs/{done_job.id}/glossary", json={"from": "c", "to": "d"})
        shared = fake_glossary_provider.glossary_dir / "glossary.txt"
        assert shared.read_text(encoding="utf-8") == "a => b\nc => d\n"

    def test_the_same_line_twice_is_not_written_twice(self, client, done_job, fake_glossary_provider):
        client.post(f"/api/jobs/{done_job.id}/glossary", json={"from": "a", "to": "b"})
        res = client.post(f"/api/jobs/{done_job.id}/glossary", json={"from": " a ", "to": "b"})
        assert res.json()["added"] is False
        assert (fake_glossary_provider.glossary_dir / "glossary.txt").read_text(encoding="utf-8") == "a => b\n"

    @pytest.mark.parametrize(
        "body",
        [
            {"from": "a => b", "to": "c"},
            {"from": "a", "to": "b => c"},
            {"from": "a\nb", "to": "c"},
            {"from": "#a", "to": "c"},
            {"from": "same", "to": "same"},
        ],
    )
    def test_a_pair_that_cannot_be_one_glossary_line_is_a_400(self, client, done_job, body):
        assert client.post(f"/api/jobs/{done_job.id}/glossary", json=body).status_code == 400

    @pytest.mark.parametrize("body", [{"from": "", "to": "b"}, {"to": "b"}, {"from": "a"}])
    def test_a_missing_side_is_refused_by_the_model(self, client, done_job, body):
        assert client.post(f"/api/jobs/{done_job.id}/glossary", json=body).status_code == 422

    def test_an_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/nope/glossary", json={"from": "a", "to": "b"}).status_code == 404

    def test_it_needs_the_client_header_like_every_mutation(self, app, done_job, fake_glossary_provider):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.post(f"/api/jobs/{done_job.id}/glossary", json={"from": "a", "to": "b"}).status_code == 403
        assert not (fake_glossary_provider.glossary_dir / "glossary.txt").exists()

    def test_a_provider_that_cannot_reach_the_shared_file_says_so(self, client, done_job, app):
        app.state.glossary_provider = FakeGlossaryProvider()
        res = client.post(f"/api/jobs/{done_job.id}/glossary", json={"from": "a", "to": "b"})
        assert res.status_code == 501


def test_the_record_on_disk_is_the_one_the_route_answered_with(client, done_job):
    _write_transcript(done_job)
    body = _patch(
        client,
        done_job,
        0,
        {"op": "set_text", "index": 2, "text": "herramienta"},
        {"op": "retime", "index": 3, "end": 1.5},
    ).json()
    saved = json.loads((Path(done_job.output_dir) / "clip.transcript.json").read_text(encoding="utf-8"))
    assert saved["revision"] == body["revision"] == 2
    assert saved["words"][2]["t"] == "herramienta"
    assert saved["meta"][3]["retimed"] is True
