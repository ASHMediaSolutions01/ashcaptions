"""Tests for the in-app update banner and apply flow (spec 11.4). All
against fakes -- no network, no zip extraction, no restart helper."""
from __future__ import annotations

from pathlib import Path

from ash_captions.web.models import JobOptions, JobStatus, UpdateApplyStatus

from .fakes import FakeUpdateInfo


def test_no_update_available_is_a_plain_null(client):
    res = client.get("/api/update")
    assert res.status_code == 200
    assert res.json() is None


def test_update_available_is_surfaced(client, fake_update_state):
    fake_update_state.set(FakeUpdateInfo(version="2.0.0", notes="New styles", size_bytes=500_000_000))

    res = client.get("/api/update")

    assert res.status_code == 200
    body = res.json()
    assert body["version"] == "2.0.0"
    assert body["notes"] == "New styles"
    assert body["size_bytes"] == 500_000_000
    assert body["blocked_reason"] is None


def test_update_blocked_while_a_job_is_running(client, fake_update_state, fake_queue):
    fake_update_state.set(FakeUpdateInfo())
    submitted = fake_queue.submit(Path("clip.mp4"), JobOptions(language="en", preset="POP"))
    fake_queue.force_status(submitted.id, JobStatus.RUNNING)

    res = client.get("/api/update")

    assert res.status_code == 200
    assert "still running" in res.json()["blocked_reason"]


def test_apply_with_no_update_available_is_404(client):
    res = client.post("/api/update/apply")
    assert res.status_code == 404


def test_apply_is_a_single_click_no_confirmation_needed(client, fake_update_state, fake_update_applier):
    """The button IS the consent -- one POST is enough to start applying,
    no second confirmation step in the API."""
    fake_update_state.set(FakeUpdateInfo(version="2.0.0"))

    res = client.post("/api/update/apply")

    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "pending"
    assert len(fake_update_applier.submitted) == 1
    assert fake_update_applier.submitted[0].version == "2.0.0"


def test_apply_refused_while_a_job_is_running(client, fake_update_state, fake_queue, fake_update_applier):
    fake_update_state.set(FakeUpdateInfo())
    submitted = fake_queue.submit(Path("clip.mp4"), JobOptions(language="en", preset="POP"))
    fake_queue.force_status(submitted.id, JobStatus.RUNNING)

    res = client.post("/api/update/apply")

    assert res.status_code == 409
    assert "still running" in res.json()["detail"]
    assert fake_update_applier.submitted == []  # never even reached the applier


def test_poll_apply_job_status(client, fake_update_state, fake_update_applier):
    fake_update_state.set(FakeUpdateInfo())
    job_id = client.post("/api/update/apply").json()["id"]

    pending = client.get(f"/api/update/apply/{job_id}")
    assert pending.json()["status"] == "pending"

    fake_update_applier.force_status(job_id, UpdateApplyStatus.DOWNLOADING)
    assert client.get(f"/api/update/apply/{job_id}").json()["status"] == "downloading"

    fake_update_applier.force_status(job_id, UpdateApplyStatus.FAILED, error="Download failed: timed out")
    failed = client.get(f"/api/update/apply/{job_id}")
    assert failed.json()["status"] == "failed"
    assert "timed out" in failed.json()["error"]


def test_get_unknown_apply_job_is_404(client):
    res = client.get("/api/update/apply/does-not-exist")
    assert res.status_code == 404
