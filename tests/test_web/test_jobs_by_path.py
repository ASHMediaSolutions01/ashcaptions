from __future__ import annotations

import json


def _submit_by_path(client, path, **overrides):
    body = {
        "path": str(path),
        "language": "en",
        "dialect": "en-US",
        "preset": "POP",
        "burn_in": False,
        "translate_to_english": False,
    }
    body.update(overrides)
    return client.post("/api/jobs/by-path", content=json.dumps(body), headers={"Content-Type": "application/json"})


class TestSubmitByPath:
    def test_valid_path_is_queued_without_copying(self, client, fake_queue, tmp_path, app):
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"some video bytes")

        res = _submit_by_path(client, video)
        assert res.status_code == 201
        body = res.json()
        assert body["filename"] == "clip.mp4"
        assert body["status"] == "pending"
        assert fake_queue.get_job(body["id"]) is not None

        # The file must be read in place, never copied into incoming_dir.
        assert list(app.state.incoming_dir.glob("**/*")) == []
        assert video.exists()
        assert video.read_bytes() == b"some video bytes"

    def test_strips_surrounding_quotes(self, client, tmp_path):
        video = tmp_path / "clip.mov"
        video.write_bytes(b"data")
        quoted = f'"{video}"'

        res = _submit_by_path(client, quoted)
        assert res.status_code == 201
        assert res.json()["filename"] == "clip.mov"

    def test_rejects_missing_path(self, client, tmp_path):
        missing = tmp_path / "does-not-exist.mp4"
        res = _submit_by_path(client, missing)
        assert res.status_code == 400

    def test_rejects_directory(self, client, tmp_path):
        res = _submit_by_path(client, tmp_path)
        assert res.status_code == 400

    def test_rejects_non_video_extension(self, client, tmp_path):
        doc = tmp_path / "notes.txt"
        doc.write_text("hello")
        res = _submit_by_path(client, doc)
        assert res.status_code == 400

    def test_rejects_bad_options_before_touching_disk(self, client, tmp_path, fake_queue):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"data")
        res = _submit_by_path(client, video, language="klingon")
        assert res.status_code == 400
        assert fake_queue.list_jobs() == []

    def test_rejects_empty_path_string(self, client):
        res = _submit_by_path(client, "   ")
        assert res.status_code == 400
