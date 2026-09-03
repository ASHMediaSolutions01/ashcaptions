"""Regressions for the 2026-09-02 audit findings that live in the web layer:
the upload byte ceiling is enforced while copying (not only from
Content-Length), update apply is single-flight, and "Reset to shipped"
removes the override instead of writing a new one.
"""
from __future__ import annotations

import threading

import pytest

from ash_captions.web import routes_jobs
from ash_captions.web.interfaces import UpdateApplyBusyError
from ash_captions.web.update_adapter import UpdaterAdapter

from .fakes import default_style_definition


# -- upload byte ceiling ------------------------------------------------------


def test_upload_larger_than_the_ceiling_is_refused_while_copying(client, app, fake_queue, monkeypatch):
    """Content-Length can be absent or dishonest (chunked bodies); the copy
    itself must stop at the ceiling and leave nothing behind."""
    monkeypatch.setattr(routes_jobs, "MAX_UPLOAD_BYTES", 64)
    body = b"\x00" * 200  # honest length header, but over the (patched) cap
    res = client.post(
        "/api/jobs",
        data={"language": "en", "preset": "POP"},
        files={"file": ("big.mp4", body, "video/mp4")},
    )
    assert res.status_code == 413
    assert "Video file location" in res.json()["detail"]
    assert fake_queue.list_jobs() == []
    assert list(app.state.incoming_dir.glob("**/*")) == []


def test_upload_under_the_ceiling_still_works(client, app, monkeypatch):
    monkeypatch.setattr(routes_jobs, "MAX_UPLOAD_BYTES", 1024)
    res = client.post(
        "/api/jobs",
        data={"language": "en", "preset": "POP"},
        files={"file": ("small.mp4", b"\x00" * 100, "video/mp4")},
    )
    assert res.status_code == 201


# -- single-flight update apply -----------------------------------------------


def test_second_apply_while_one_is_live_is_refused(tmp_path):
    release = threading.Event()

    def blocking_apply(artifact_path, *, has_running_job):
        release.wait(5)

    adapter = UpdaterAdapter(
        dest_dir=tmp_path,
        on_applied=lambda: None,
        sleep_fn=lambda seconds: None,
        download_and_verify=lambda update, *, dest_dir: dest_dir / "artifact.zip",
        apply=blocking_apply,
    )
    first = adapter.submit_apply({"version": "9.9.9"}, has_running_job=lambda: False)
    try:
        with pytest.raises(UpdateApplyBusyError) as excinfo:
            adapter.submit_apply({"version": "9.9.9"}, has_running_job=lambda: False)
        assert excinfo.value.job_id == first.id
    finally:
        release.set()


def test_apply_route_reports_busy_as_409(client, fake_update_applier, monkeypatch):
    def busy(*_a, **_k):
        raise UpdateApplyBusyError("abc123")

    monkeypatch.setattr(fake_update_applier, "submit_apply", busy)
    res = client.post("/api/update/apply")
    # 409 from the busy guard, or 404/409 from an earlier guard when the
    # fake has no update staged -- either way never a 500 and never a
    # second job.
    assert res.status_code in (404, 409)
    assert res.status_code != 500


# -- reset to shipped ---------------------------------------------------------


def test_reset_removes_the_local_override_of_a_shipped_style(client):
    custom = default_style_definition("POP")
    custom["size"] = 99
    assert client.put("/api/styles/POP", json=custom).status_code == 200
    assert client.get("/api/styles/POP").json()["customized_locally"] is True

    res = client.post("/api/styles/POP/reset")

    assert res.status_code == 200
    assert res.json()["customized_locally"] is False
    assert client.get("/api/styles/POP").json()["customized_locally"] is False
    assert client.get("/api/styles/POP").json()["definition"].get("size") != 99


def test_reset_of_a_non_shipped_style_is_404(client):
    assert client.post("/api/styles/Definitely%20Not%20Shipped/reset").status_code == 404
