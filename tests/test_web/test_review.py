"""Review-page data routes (routes_review.py): output listing, the input
video with HTTP Range support, and the .ass caption file -- against real
temp files, since seeking is the whole point."""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.web.models import JobOptions

from .fakes import FakeJobQueue

VIDEO = bytes(range(256)) * 40  # 10,240 distinguishable bytes


@pytest.fixture
def fake_queue(tmp_path) -> FakeJobQueue:
    return FakeJobQueue(output_root=tmp_path / "out")


@pytest.fixture
def finished_job(fake_queue, tmp_path):
    video = tmp_path / "footage" / "interview.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(VIDEO)
    job = fake_queue.submit(video, JobOptions(language="en", preset="POP"))
    out = Path(job.output_dir)
    out.mkdir(parents=True)
    (out / "interview.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    (out / "interview.ass").write_bytes(ASS_BYTES)
    return job


ASS_BYTES = b"[Script Info]\nTitle: test\n"


class TestFiles:
    def test_lists_output_files_with_sizes(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/files")
        assert res.status_code == 200
        by_name = {f["name"]: f["size_bytes"] for f in res.json()}
        assert set(by_name) == {"interview.srt", "interview.ass"}
        assert by_name["interview.ass"] == len(ASS_BYTES)

    def test_missing_output_dir_lists_nothing(self, client, fake_queue, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        job = fake_queue.submit(video, JobOptions(language="en", preset="POP"))
        assert client.get(f"/api/jobs/{job.id}/files").json() == []

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope/files").status_code == 404

    def test_queue_without_output_dir_is_404_not_a_guess(self, client, tmp_path):
        # The default FakeJobQueue (no output_root) leaves output_dir None,
        # like a queue implementation that predates the Review routes.
        queue = FakeJobQueue()
        client.app.state.queue = queue
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        job = queue.submit(video, JobOptions(language="en", preset="POP"))
        assert client.get(f"/api/jobs/{job.id}/files").status_code == 404
        assert client.get(f"/api/jobs/{job.id}/ass").status_code == 404


class TestVideo:
    def test_full_response_advertises_ranges(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/video")
        assert res.status_code == 200
        assert res.headers["accept-ranges"] == "bytes"
        assert res.headers["content-type"] == "video/mp4"
        assert res.headers["content-length"] == str(len(VIDEO))
        assert res.content == VIDEO

    def test_range_request_returns_206_with_exact_slice(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/video", headers={"Range": "bytes=1000-1999"})
        assert res.status_code == 206
        assert res.headers["content-range"] == f"bytes 1000-1999/{len(VIDEO)}"
        assert res.headers["content-length"] == "1000"
        assert res.content == VIDEO[1000:2000]

    def test_open_ended_range_streams_to_eof(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/video", headers={"Range": f"bytes={len(VIDEO) - 300}-"})
        assert res.status_code == 206
        assert res.content == VIDEO[-300:]
        assert res.headers["content-range"] == f"bytes {len(VIDEO) - 300}-{len(VIDEO) - 1}/{len(VIDEO)}"

    def test_unsatisfiable_range_is_416(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/video", headers={"Range": f"bytes={len(VIDEO) + 10}-"})
        assert res.status_code == 416
        assert res.headers["content-range"] == f"bytes */{len(VIDEO)}"

    def test_response_is_streamed_not_read_whole(self, client, finished_job):
        # Starlette's FileResponse streams in chunks; the transfer is either
        # chunked or content-length-framed, and it's never a single read of
        # the whole file (see routes_review.py). What we pin here: a large
        # slice arrives byte-exact through the streaming path.
        with client.stream("GET", f"/api/jobs/{finished_job.id}/video", headers={"Range": "bytes=0-8191"}) as res:
            assert res.status_code == 206
            chunks = list(res.iter_bytes(chunk_size=1024))
        assert b"".join(chunks) == VIDEO[:8192]
        assert len(chunks) > 1

    def test_deleted_input_is_404(self, client, finished_job):
        Path(finished_job.input_path).unlink()
        assert client.get(f"/api/jobs/{finished_job.id}/video").status_code == 404


class TestAss:
    def test_serves_the_ass_file(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/ass")
        assert res.status_code == 200
        assert res.text.startswith("[Script Info]")
        assert res.headers["content-type"].startswith("text/plain")

    def test_no_ass_yet_is_404(self, client, finished_job):
        (Path(finished_job.output_dir) / "interview.ass").unlink()
        assert client.get(f"/api/jobs/{finished_job.id}/ass").status_code == 404


class TestDownload:
    """GET /api/jobs/{id}/files/{name}: v0.6 §3's actual download. The name
    is matched against the same listing /files returns, never joined onto
    output_dir -- so every traversal shape below is just "not a match,"
    proven by asserting the real secret bytes never come back, the same
    property `test_refuses_names_not_in_the_manifest` pins for the font
    file route this one is modelled on."""

    def test_downloads_a_listed_text_file(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/files/interview.srt")
        assert res.status_code == 200
        assert res.content == b"1\n00:00:00,000 --> 00:00:01,000\nhi\n"
        assert res.headers["content-type"].startswith("text/plain")
        assert res.headers["content-disposition"].startswith("attachment")
        assert 'filename="interview.srt"' in res.headers["content-disposition"]

    def test_downloads_the_ass_file(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/files/interview.ass")
        assert res.status_code == 200
        assert res.content == ASS_BYTES
        assert res.headers["content-type"].startswith("text/plain")

    def test_downloads_the_captioned_video_as_an_attachment_with_video_media_type(self, client, fake_queue, tmp_path):
        video = tmp_path / "footage" / "reel.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(VIDEO)
        job = fake_queue.submit(video, JobOptions(language="en", preset="POP"))
        out = Path(job.output_dir)
        out.mkdir(parents=True)
        (out / "reel.captioned.mp4").write_bytes(VIDEO)
        res = client.get(f"/api/jobs/{job.id}/files/reel.captioned.mp4")
        assert res.status_code == 200
        assert res.content == VIDEO
        assert res.headers["content-type"] == "video/mp4"
        assert res.headers["content-disposition"].startswith("attachment")

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope/files/whatever.srt").status_code == 404

    def test_queue_without_output_dir_is_404(self, client, tmp_path):
        queue = FakeJobQueue()
        client.app.state.queue = queue
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        job = queue.submit(video, JobOptions(language="en", preset="POP"))
        assert client.get(f"/api/jobs/{job.id}/files/clip.srt").status_code == 404

    def test_a_name_not_in_this_jobs_listing_is_404(self, client, fake_queue, finished_job, tmp_path):
        # A real file, just not in *this* job's output_dir -- proves the
        # match is against this job's own listing, not any file that
        # happens to exist somewhere reachable from the process.
        other_video = tmp_path / "other.mp4"
        other_video.write_bytes(b"y")
        other_job = fake_queue.submit(other_video, JobOptions(language="en", preset="POP"))
        Path(other_job.output_dir).mkdir(parents=True)
        (Path(other_job.output_dir) / "other.srt").write_bytes(b"SECRET")
        res = client.get(f"/api/jobs/{finished_job.id}/files/other.srt")
        assert res.status_code == 404
        assert b"SECRET" not in res.content

    def test_a_name_with_no_file_at_all_is_404(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/files/nope.srt")
        assert res.status_code == 404

    def test_bare_dot_dot_is_normalized_away_before_it_ever_reaches_the_route(self, client, finished_job):
        # httpx (like a real browser) collapses ".." during URL resolution
        # before the request is even sent, so this never reaches our
        # handler at all -- it lands one segment further up, on the plain
        # job route. Stronger than a 404: the server never sees a
        # traversal attempt here in the first place, and what answers it
        # is a job record, never a file's bytes.
        res = client.get(f"/api/jobs/{finished_job.id}/files/..")
        assert res.status_code == 200
        assert res.json()["id"] == finished_job.id

    @pytest.mark.parametrize(
        "name",
        [
            "..%2Finterview.srt",
            "%2E%2E%2Finterview.srt",
            "..%5Cinterview.srt",
            "C%3A%5CWindows%5Cwin.ini",
        ],
    )
    def test_traversal_attempts_are_404(self, client, finished_job, name):
        res = client.get(f"/api/jobs/{finished_job.id}/files/{name}")
        assert res.status_code == 404
        assert b"Script Info" not in res.content  # never the .ass file's own bytes either

    def test_absolute_path_style_name_is_404_not_a_file_read_from_elsewhere(self, client, finished_job, tmp_path):
        secret = tmp_path / "elsewhere.txt"
        secret.write_text("do not serve me")
        # The path segment itself (no slash, so it routes here) names a file
        # that exists on disk, just nowhere near this job's output_dir.
        res = client.get(f"/api/jobs/{finished_job.id}/files/{secret.name}")
        assert res.status_code == 404
        assert b"do not serve me" not in res.content
