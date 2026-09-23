"""The Queue's archive: GET /api/jobs searched and paged, and a burn that
can decide "behind the speaker" from the Studio.

Groundwork for the reshaped Queue (2026-09-23): editors keep the recent
few in front of them and search the rest instead of scrolling a hundred
identical "ig reel.mp4" cards.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ash_captions.web.models import JobStatus

from .test_jobs import VIDEO_BYTES, _submit


def _seed(client, fake_queue, names):
    ids = []
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i, name in enumerate(names):
        job = _submit(client, files={"file": (name, VIDEO_BYTES, "video/mp4")}).json()
        fake_queue.force_status(job["id"], JobStatus.DONE, created_at=base + timedelta(minutes=i))
        ids.append(job["id"])
    return ids


class TestSearch:
    def test_q_matches_the_file_name_case_insensitively(self, client, fake_queue):
        _seed(client, fake_queue, ["Client's reel, v2.mp4", "entrevista-es.mp4", "five-min.mp4"])
        rows = client.get("/api/jobs", params={"q": "REEL"}).json()
        assert [r["filename"] for r in rows] == ["Client's reel, v2.mp4"]

    def test_the_client_typed_in_the_drawer_is_searchable(self, client, fake_queue, tmp_path):
        video = tmp_path / "footage" / "reel.mp4"
        video.parent.mkdir()
        video.write_bytes(VIDEO_BYTES)
        res = client.post("/api/jobs/by-path", json={"path": str(video), "language": "en", "client": "Acme"})
        assert res.status_code == 201, res.text
        fake_queue.force_status(res.json()["id"], JobStatus.DONE)
        assert [r["filename"] for r in client.get("/api/jobs", params={"q": "acme"}).json()] == ["reel.mp4"]

    def test_no_match_is_an_empty_list_not_an_error(self, client, fake_queue):
        _seed(client, fake_queue, ["a.mp4"])
        assert client.get("/api/jobs", params={"q": "zzz"}).json() == []

    def test_a_blank_query_is_the_plain_list(self, client, fake_queue):
        _seed(client, fake_queue, ["a.mp4", "b.mp4"])
        assert len(client.get("/api/jobs", params={"q": "  "}).json()) == 2


class TestPaging:
    def test_offset_and_limit_walk_the_list_newest_first(self, client, fake_queue):
        _seed(client, fake_queue, [f"job{i}.mp4" for i in range(5)])
        first = client.get("/api/jobs", params={"limit": 2}).json()
        second = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()
        assert [r["filename"] for r in first] == ["job4.mp4", "job3.mp4"]
        assert [r["filename"] for r in second] == ["job2.mp4", "job1.mp4"]

    def test_bad_paging_values_are_refused_plainly(self, client):
        assert client.get("/api/jobs", params={"offset": -1}).status_code == 400
        assert client.get("/api/jobs", params={"limit": 0}).status_code == 400
        assert client.get("/api/jobs", params={"limit": 10_000}).status_code == 400


class TestAnOlderQueueStillAnswers:
    def test_search_and_paging_are_applied_here_when_the_queue_cannot(self, client, fake_queue, monkeypatch):
        """A queue implementation that predates the archive has a plain
        list_jobs(); the route still searches and pages what it returns."""
        _seed(client, fake_queue, ["x-one.mp4", "y-two.mp4", "x-three.mp4"])
        rows = fake_queue.list_jobs()

        def plain_list_jobs():
            return rows

        monkeypatch.setattr(fake_queue, "list_jobs", plain_list_jobs)
        found = client.get("/api/jobs", params={"q": "x-"}).json()
        assert [r["filename"] for r in found] == ["x-three.mp4", "x-one.mp4"]
        assert [r["filename"] for r in client.get("/api/jobs", params={"offset": 2}).json()] == ["x-one.mp4"]


class TestBehindSpeakerFromTheStudio:
    def test_the_burn_body_accepts_it(self, client, fake_queue):
        job = _submit(client).json()
        fake_queue.force_status(job["id"], JobStatus.DONE)
        res = client.post(f"/api/jobs/{job['id']}/burn", json={"preset": "POP", "behind_speaker": True})
        assert res.status_code == 201

    def test_left_out_it_changes_nothing(self, client, fake_queue):
        job = _submit(client).json()
        fake_queue.force_status(job["id"], JobStatus.DONE)
        res = client.post(f"/api/jobs/{job['id']}/burn", json={"preset": "POP"})
        assert res.status_code == 201
