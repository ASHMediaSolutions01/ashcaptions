from __future__ import annotations

from ash_captions.web.models import JobStatus

VIDEO_BYTES = b"not-a-real-video-but-good-enough-for-a-content-check"


def _submit(client, **overrides):
    data = {
        "language": "en",
        "dialect": "en-US",
        "preset": "POP",
        "burn_in": "false",
        "translate_to_english": "false",
    }
    data.update(overrides.pop("data", {}))
    files = overrides.pop("files", {"file": ("clip.mp4", VIDEO_BYTES, "video/mp4")})
    return client.post("/api/jobs", data=data, files=files)


class TestLanguages:
    def test_list_languages_returns_catalogue(self, client, fake_catalogue):
        res = client.get("/api/languages")
        assert res.status_code == 200
        codes = {lang["code"] for lang in res.json()}
        assert codes == {lang.code for lang in fake_catalogue.list_languages()}

    def test_dialects_are_scoped_to_their_language(self, client):
        res = client.get("/api/languages")
        by_code = {lang["code"]: lang for lang in res.json()}
        assert {d["code"] for d in by_code["en"]["dialects"]} == {"en-US", "en-UK"}
        assert by_code["fr"]["dialects"] == []


class TestSubmitJob:
    def test_valid_submission_is_queued(self, client, fake_queue):
        res = _submit(client)
        assert res.status_code == 201
        body = res.json()
        assert body["filename"] == "clip.mp4"
        assert body["status"] == "pending"
        assert body["options"]["language"] == "en"
        assert body["options"]["dialect"] == "en-US"
        assert body["options"]["preset"] == "POP"
        assert fake_queue.get_job(body["id"]) is not None

    def test_submission_saves_file_into_incoming_dir(self, client, app):
        res = _submit(client)
        assert res.status_code == 201
        saved = list(app.state.incoming_dir.glob("*/clip.mp4"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == VIDEO_BYTES

    def test_preset_is_normalized_to_uppercase(self, client):
        res = _submit(client, data={"preset": "pop"})
        assert res.status_code == 201
        assert res.json()["options"]["preset"] == "POP"

    def test_dialect_is_optional(self, client):
        res = client.post(
            "/api/jobs",
            data={"language": "fr", "preset": "CLEAN", "burn_in": "false", "translate_to_english": "false"},
            files={"file": ("clip.mov", VIDEO_BYTES, "video/quicktime")},
        )
        assert res.status_code == 201
        assert res.json()["options"]["dialect"] is None

    def test_rejects_unknown_language(self, client, fake_queue):
        res = _submit(client, data={"language": "klingon"})
        assert res.status_code == 400
        assert fake_queue.list_jobs() == []

    def test_rejects_dialect_not_in_language(self, client):
        # es-MX is a Spanish dialect, not English.
        res = _submit(client, data={"language": "en", "dialect": "es-MX"})
        assert res.status_code == 400

    def test_rejects_unknown_preset(self, client):
        res = _submit(client, data={"preset": "FANCY"})
        assert res.status_code == 400

    def test_rejects_non_video_extension(self, client):
        res = _submit(client, files={"file": ("notes.txt", b"hello", "text/plain")})
        assert res.status_code == 400

    def test_rejects_empty_file(self, client):
        res = _submit(client, files={"file": ("clip.mp4", b"", "video/mp4")})
        assert res.status_code == 400


class TestListJobs:
    def test_empty_queue(self, client):
        res = client.get("/api/jobs")
        assert res.status_code == 200
        assert res.json() == []

    def test_lists_submitted_jobs(self, client):
        _submit(client, data={"language": "en"}, files={"file": ("a.mp4", VIDEO_BYTES, "video/mp4")})
        _submit(client, data={"language": "es", "dialect": "es-MX"}, files={"file": ("b.mp4", VIDEO_BYTES, "video/mp4")})
        res = client.get("/api/jobs")
        assert res.status_code == 200
        filenames = {job["filename"] for job in res.json()}
        assert filenames == {"a.mp4", "b.mp4"}


class TestRetry:
    def test_retry_requeues_a_failed_job(self, client, fake_queue):
        job = _submit(client).json()
        fake_queue.force_status(job["id"], JobStatus.FAILED, error="ffmpeg exploded")

        res = client.post(f"/api/jobs/{job['id']}/retry")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "pending"
        assert body["error"] is None

    def test_retry_unknown_job_is_404(self, client):
        res = client.post("/api/jobs/does-not-exist/retry")
        assert res.status_code == 404

    def test_retry_non_failed_job_is_409(self, client):
        job = _submit(client).json()  # still pending
        res = client.post(f"/api/jobs/{job['id']}/retry")
        assert res.status_code == 409
