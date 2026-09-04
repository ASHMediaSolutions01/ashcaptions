"""Desktop and housekeeping routes (routes_desktop.py): the native file
picker behind Browse..., job thumbnails (a real temp JPEG on disk, the
generation step driven through a fake ffmpeg runner), Remove from list,
and Open folder -- each with the client-header rule every mutation has."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ash_captions.web import thumbs
from ash_captions.web.models import JobOptions, JobStatus

from .conftest import LOCAL_BASE_URL
from .fakes import FakeFilePicker, FakeJobQueue

JPEG = b"\xff\xd8\xff\xe0" + b"J" * 200 + b"\xff\xd9"
VIDEO = b"v" * 4096


@pytest.fixture
def fake_queue(tmp_path) -> FakeJobQueue:
    return FakeJobQueue(output_root=tmp_path / "out")


@pytest.fixture
def done_job(fake_queue, tmp_path):
    video = tmp_path / "footage" / "reel.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(VIDEO)
    job = fake_queue.submit(video, JobOptions(language="en", preset="POP"))
    fake_queue.force_status(job.id, JobStatus.DONE, progress=1.0)
    Path(job.output_dir).mkdir(parents=True)
    return fake_queue.get_job(job.id)


class TestPickFile:
    def test_returns_the_chosen_path(self, client, fake_file_picker):
        fake_file_picker.result = r"D:\Projects\acme\reel.mp4"
        res = client.post("/api/pick-file")
        assert res.status_code == 200
        assert res.json() == {"path": r"D:\Projects\acme\reel.mp4"}
        assert fake_file_picker.calls == 1

    def test_cancelled_dialog_is_null_not_an_error(self, client, fake_file_picker):
        fake_file_picker.result = None
        res = client.post("/api/pick-file")
        assert res.status_code == 200
        assert res.json() == {"path": None}

    def test_second_dialog_while_one_is_open_is_409(self, client):
        client.app.state.file_picker = FakeFilePicker(busy=True)
        res = client.post("/api/pick-file")
        assert res.status_code == 409
        assert "already open" in res.json()["detail"]

    def test_requires_the_client_header(self, app, fake_file_picker):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.post("/api/pick-file").status_code == 403
        assert fake_file_picker.calls == 0


class TestThumb:
    def test_serves_an_existing_thumb_with_caching(self, client, done_job):
        (Path(done_job.output_dir) / thumbs.THUMB_NAME).write_bytes(JPEG)
        res = client.get(f"/api/jobs/{done_job.id}/thumb")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.headers["cache-control"] == "private, max-age=86400"
        assert res.content == JPEG

    def test_generates_once_from_the_source_at_ten_percent(self, client, done_job, monkeypatch):
        commands: list[list[str]] = []

        def fake_run(command, timeout):
            commands.append(command)
            if "ffprobe" in Path(command[0]).name:
                return _Completed(0, "40.0\n")
            Path(command[-1]).write_bytes(JPEG)  # ffmpeg writes the .part file
            return _Completed(0, "")

        monkeypatch.setattr(thumbs, "find_binary", lambda name: Path(f"C:/bin/{name}.exe"))
        monkeypatch.setattr(thumbs, "_run", fake_run)

        first = client.get(f"/api/jobs/{done_job.id}/thumb")
        assert first.status_code == 200 and first.content == JPEG
        assert (Path(done_job.output_dir) / ".thumb.jpg").is_file()
        assert not list(Path(done_job.output_dir).glob("*.part"))
        ffmpeg = next(c for c in commands if "ffmpeg" in Path(c[0]).name)
        assert ffmpeg[ffmpeg.index("-ss") + 1] == "4.000"  # 10% of 40 s
        assert ffmpeg[ffmpeg.index("-i") + 1] == done_job.input_path
        assert "scale=320:-2" in ffmpeg

        commands.clear()
        assert client.get(f"/api/jobs/{done_job.id}/thumb").status_code == 200
        assert commands == []  # served from disk the second time

    def test_falls_back_to_the_burned_output_when_the_source_is_gone(self, client, done_job, monkeypatch):
        Path(done_job.input_path).unlink()
        burned = Path(done_job.output_dir) / "reel.captioned.mp4"
        burned.write_bytes(VIDEO)
        sources: list[str] = []

        def fake_run(command, timeout):
            if "ffprobe" in Path(command[0]).name:
                return _Completed(0, "")
            sources.append(command[command.index("-i") + 1])
            Path(command[-1]).write_bytes(JPEG)
            return _Completed(0, "")

        monkeypatch.setattr(thumbs, "find_binary", lambda name: Path(f"C:/bin/{name}.exe"))
        monkeypatch.setattr(thumbs, "_run", fake_run)
        assert client.get(f"/api/jobs/{done_job.id}/thumb").status_code == 200
        assert sources == [str(burned)]

    def test_no_source_at_all_is_404(self, client, done_job):
        Path(done_job.input_path).unlink()
        assert client.get(f"/api/jobs/{done_job.id}/thumb").status_code == 404

    def test_failed_ffmpeg_leaves_nothing_behind_and_is_404(self, client, done_job, monkeypatch):
        monkeypatch.setattr(thumbs, "find_binary", lambda name: Path(f"C:/bin/{name}.exe"))
        monkeypatch.setattr(thumbs, "_run", lambda command, timeout: _Completed(1, ""))
        assert client.get(f"/api/jobs/{done_job.id}/thumb").status_code == 404
        assert list(Path(done_job.output_dir).iterdir()) == []

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope/thumb").status_code == 404


class TestRemove:
    def test_removes_a_finished_job_but_keeps_its_files(self, client, done_job, fake_queue):
        keep = Path(done_job.output_dir) / "reel.srt"
        keep.write_text("1\n", encoding="utf-8")
        res = client.delete(f"/api/jobs/{done_job.id}")
        assert res.status_code == 204
        assert fake_queue.get_job(done_job.id) is None
        assert keep.is_file()

    def test_live_job_is_409(self, client, done_job, fake_queue):
        for status in (JobStatus.PENDING, JobStatus.RUNNING):
            fake_queue.force_status(done_job.id, status)
            res = client.delete(f"/api/jobs/{done_job.id}")
            assert res.status_code == 409, status
        assert fake_queue.get_job(done_job.id) is not None

    def test_failed_job_can_be_removed(self, client, done_job, fake_queue):
        fake_queue.force_status(done_job.id, JobStatus.FAILED, error="boom")
        assert client.delete(f"/api/jobs/{done_job.id}").status_code == 204

    def test_unknown_job_is_404(self, client):
        assert client.delete("/api/jobs/nope").status_code == 404

    def test_queue_without_remove_job_is_501(self, client, done_job, fake_queue):
        class ReadOnlyQueue:
            list_jobs = fake_queue.list_jobs
            get_job = fake_queue.get_job

        client.app.state.queue = ReadOnlyQueue()
        assert client.delete(f"/api/jobs/{done_job.id}").status_code == 501

    def test_requires_the_client_header(self, app, done_job, fake_queue):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.delete(f"/api/jobs/{done_job.id}").status_code == 403
        assert fake_queue.get_job(done_job.id) is not None


class TestReveal:
    def test_selects_the_burned_output_when_there_is_one(self, client, done_job, fake_revealer):
        burned = Path(done_job.output_dir) / "reel.captioned.mp4"
        burned.write_bytes(VIDEO)
        assert client.post(f"/api/jobs/{done_job.id}/reveal").status_code == 204
        assert fake_revealer.revealed == [burned]

    def test_opens_the_folder_without_a_burned_output(self, client, done_job, fake_revealer):
        assert client.post(f"/api/jobs/{done_job.id}/reveal").status_code == 204
        assert fake_revealer.revealed == [Path(done_job.output_dir)]

    def test_missing_folder_is_404(self, client, done_job, fake_revealer):
        Path(done_job.output_dir).rmdir()
        assert client.post(f"/api/jobs/{done_job.id}/reveal").status_code == 404
        assert fake_revealer.revealed == []

    def test_never_takes_a_path_from_the_request(self, client, done_job, fake_revealer, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        res = client.post(f"/api/jobs/{done_job.id}/reveal", json={"path": str(elsewhere)})
        assert res.status_code == 204
        assert fake_revealer.revealed == [Path(done_job.output_dir)]

    def test_requires_the_client_header(self, app, done_job, fake_revealer):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.post(f"/api/jobs/{done_job.id}/reveal").status_code == 403
        assert fake_revealer.revealed == []


class _Completed:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
