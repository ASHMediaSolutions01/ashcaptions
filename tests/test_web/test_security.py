"""Origin/Host defence (web/security.py): a page the editor has open in the
same browser must not be able to enqueue jobs or start an update."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from .conftest import CLIENT_HEADERS, LOCAL_BASE_URL
from .test_jobs import VIDEO_BYTES

FORM = {"language": "en", "preset": "POP", "burn_in": "false", "translate_to_english": "false"}
FILES = {"file": ("clip.mp4", VIDEO_BYTES, "video/mp4")}


def _foreign_page(app) -> TestClient:
    """A client with a loopback Host but none of the app's own headers --
    what a cross-site form post looks like once it reaches the server."""
    return TestClient(app, base_url=LOCAL_BASE_URL)


class TestOrigin:
    def test_multipart_post_from_foreign_origin_is_rejected(self, app, fake_queue):
        res = _foreign_page(app).post(
            "/api/jobs", data=FORM, files=FILES, headers={"Origin": "https://evil.example", **CLIENT_HEADERS}
        )
        assert res.status_code == 403
        assert "other web pages" in res.json()["detail"]
        assert fake_queue.list_jobs() == []

    def test_json_post_from_foreign_origin_is_rejected(self, app, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        res = _foreign_page(app).post(
            "/api/jobs/by-path",
            content=json.dumps({"path": str(video), "language": "en", "preset": "POP"}),
            headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1.evil.example", **CLIENT_HEADERS},
        )
        assert res.status_code == 403

    def test_update_apply_from_foreign_origin_is_rejected(self, app, fake_update_applier):
        res = _foreign_page(app).post("/api/update/apply", headers={"Origin": "http://localhost.evil", **CLIENT_HEADERS})
        assert res.status_code == 403
        assert fake_update_applier.submitted == []

    def test_same_origin_post_on_any_local_port_is_accepted(self, app):
        for origin in ("http://127.0.0.1:8756", "http://localhost:8791", "http://127.0.0.1"):
            res = _foreign_page(app).post("/api/jobs", data=FORM, files=FILES, headers={"Origin": origin, **CLIENT_HEADERS})
            assert res.status_code == 201, origin

    def test_get_with_foreign_origin_is_still_allowed(self, app):
        # Reads are harmless (and the browser's same-origin policy hides the
        # response anyway); only mutations are gated.
        res = _foreign_page(app).get("/api/jobs", headers={"Origin": "https://evil.example"})
        assert res.status_code == 200


class TestClientHeader:
    def test_post_without_client_header_is_rejected(self, app, fake_queue):
        res = _foreign_page(app).post("/api/jobs", data=FORM, files=FILES)
        assert res.status_code == 403
        assert "X-ASH-CLIENT" in res.json()["detail"]
        assert fake_queue.list_jobs() == []

    def test_put_and_delete_without_client_header_are_rejected(self, app):
        page = _foreign_page(app)
        assert page.put("/api/styles/POP", content="{}", headers={"Content-Type": "application/json"}).status_code == 403
        assert page.delete("/api/styles/POP").status_code == 403

    def test_post_with_client_header_is_accepted(self, client):
        res = client.post("/api/jobs", data=FORM, files=FILES)
        assert res.status_code == 201

    def test_wrong_header_value_is_rejected(self, app):
        res = _foreign_page(app).post("/api/jobs", data=FORM, files=FILES, headers={"X-ASH-Client": "yes"})
        assert res.status_code == 403


class TestTrustedHost:
    def test_get_with_foreign_host_is_400(self, app):
        res = TestClient(app, base_url="http://evil.example").get("/api/jobs")
        assert res.status_code == 400

    def test_post_with_foreign_host_is_400(self, app):
        res = TestClient(app, base_url="http://evil.example", headers=CLIENT_HEADERS).post(
            "/api/jobs", data=FORM, files=FILES
        )
        assert res.status_code == 400

    def test_localhost_and_loopback_hosts_are_trusted(self, app):
        for base in ("http://localhost:8756", "http://127.0.0.1:9000", "http://127.0.0.1"):
            assert TestClient(app, base_url=base).get("/api/jobs").status_code == 200, base
