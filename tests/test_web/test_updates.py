"""Tests for the in-app update banner and apply flow (spec 11.4). All
against fakes -- no network, no zip extraction, no restart helper."""
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_apply_forwards_a_live_has_running_job_callback(client, fake_update_state, fake_queue, fake_update_applier):
    """Regression guard: app.updater.apply_update() requires `has_running_job`
    as a keyword-only argument with no default (integration's fix) -- the
    route must always pass one, and it must reflect the queue's current
    state when called, not a snapshot taken at submit time."""
    from ash_captions.web.models import JobOptions

    fake_update_state.set(FakeUpdateInfo())

    client.post("/api/update/apply")

    assert len(fake_update_applier.has_running_job_callbacks) == 1
    has_running_job = fake_update_applier.has_running_job_callbacks[0]
    assert callable(has_running_job)
    assert has_running_job() is False

    submitted = fake_queue.submit(Path("clip.mp4"), JobOptions(language="en", preset="POP"))
    fake_queue.force_status(submitted.id, JobStatus.RUNNING)
    assert has_running_job() is True  # same callback, re-evaluated live


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


class TestSourceCheckoutGate:
    """On a source checkout (or any non-frozen build) the apply step would
    robocopy /MIR a release bundle over the git repository. The web layer
    must therefore never *offer* the update there, and must refuse to apply
    one even if asked directly."""

    @pytest.fixture
    def updates_supported(self):
        return lambda: False

    def test_banner_never_shows_even_when_a_newer_version_was_found(self, client, fake_update_state):
        fake_update_state.set(FakeUpdateInfo(version="2.0.0"))
        res = client.get("/api/update")
        assert res.status_code == 200
        assert res.json() is None

    def test_apply_is_refused_with_409(self, client, fake_update_state, fake_update_applier):
        fake_update_state.set(FakeUpdateInfo(version="2.0.0"))
        res = client.post("/api/update/apply")
        assert res.status_code == 409
        assert "source checkout" in res.json()["detail"]
        assert fake_update_applier.submitted == []


def test_default_gate_is_the_runtime_check(fake_queue, fake_catalogue, fake_style_provider, fake_preview_renderer, fake_update_applier, fake_update_state, tmp_path):
    """With no `updates_supported` injected, create_app() uses
    runtime.updates_supported() -- which, under pytest (not frozen, inside
    a git checkout), says no."""
    from fastapi.testclient import TestClient

    from ash_captions.web.app import create_app

    from .conftest import CLIENT_HEADERS, LOCAL_BASE_URL

    app = create_app(
        fake_queue,
        fake_catalogue,
        style_provider=fake_style_provider,
        preview_renderer=fake_preview_renderer,
        update_applier=fake_update_applier,
        incoming_dir=tmp_path,
    )
    app.state.update_state = fake_update_state
    fake_update_state.set(FakeUpdateInfo(version="2.0.0"))
    client = TestClient(app, base_url=LOCAL_BASE_URL, headers=CLIENT_HEADERS)
    assert client.get("/api/update").json() is None
    assert client.post("/api/update/apply").status_code == 409
