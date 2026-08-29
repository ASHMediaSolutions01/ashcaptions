"""Tests for UpdaterAdapter's job bookkeeping and, specifically, the
wait-for-quiescence step team-lead flagged: the apply path must not let
the process exit (via `on_applied`) while `has_running_job()` is still
true, and must keep waiting -- unbounded, not a fixed delay -- rather than
racing a job still in flight. `download_and_verify`/`apply`/`sleep_fn` are
all injected, so no network, no zip extraction, no detached helper, and no
real sleeping happen here."""
from __future__ import annotations

import time

import pytest

from ash_captions.web.interfaces import UpdateApplyNotFoundError
from ash_captions.web.models import UpdateApplyStatus
from ash_captions.web.update_adapter import UpdaterAdapter


def _wait_until_finished(adapter, job_id, timeout=5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = adapter.get_apply_status(job_id)
        if job.status in (UpdateApplyStatus.DONE, UpdateApplyStatus.FAILED):
            return
        time.sleep(0.02)
    raise AssertionError(f"update apply job {job_id} never finished")


def _make_adapter(tmp_path, *, apply=None, on_applied=None, sleep_fn=None) -> UpdaterAdapter:
    return UpdaterAdapter(
        dest_dir=tmp_path,
        on_applied=on_applied or (lambda: None),
        sleep_fn=sleep_fn or (lambda seconds: None),
        download_and_verify=lambda update, *, dest_dir: dest_dir / "artifact.zip",
        apply=apply or (lambda artifact_path, *, has_running_job: None),
    )


def test_has_running_job_is_forwarded_to_apply_update(tmp_path):
    """The exact regression integration found: apply_update() now requires
    has_running_job as a keyword-only argument with no default -- the
    adapter must always pass the one it was given, unchanged."""
    received = {}

    def fake_apply(artifact_path, *, has_running_job):
        received["has_running_job"] = has_running_job

    sentinel = lambda: False  # noqa: E731 - identity matters here, not behavior
    adapter = _make_adapter(tmp_path, apply=fake_apply)

    job = adapter.submit_apply(object(), has_running_job=sentinel)
    _wait_until_finished(adapter, job.id)

    assert received["has_running_job"] is sentinel
    assert adapter.get_apply_status(job.id).status == UpdateApplyStatus.DONE


def test_on_applied_never_fires_while_a_job_is_still_running(tmp_path):
    """The exact bug shape team-lead described: on_applied triggers the
    process exit, so it must never run while has_running_job() is true --
    proven here by making has_running_job() flip to False only after a
    few polls, and asserting on_applied only ever observes it as False."""
    running = {"value": True}

    def has_running_job() -> bool:
        return running["value"]

    poll_count = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        poll_count["n"] += 1
        if poll_count["n"] >= 3:
            running["value"] = False  # the in-flight job "finishes"

    on_applied_observed = []

    def fake_on_applied() -> None:
        on_applied_observed.append(has_running_job())

    adapter = _make_adapter(tmp_path, on_applied=fake_on_applied, sleep_fn=fake_sleep)

    job = adapter.submit_apply(object(), has_running_job=has_running_job)
    _wait_until_finished(adapter, job.id)

    assert poll_count["n"] == 3  # kept polling until quiescent, not a single fixed delay
    assert on_applied_observed == [False]
    assert adapter.get_apply_status(job.id).status == UpdateApplyStatus.DONE


def test_quiescence_wait_has_no_cap(tmp_path):
    """Not a bounded wait: however many times has_running_job() reports
    True, the adapter keeps polling rather than giving up and exiting
    anyway -- the "unbounded" half of `worker.stop(timeout=None)`'s
    semantics, the closest equivalent reachable without a real JobWorker."""
    remaining_true_polls = {"n": 50}

    def has_running_job() -> bool:
        return remaining_true_polls["n"] > 0

    def fake_sleep(seconds: float) -> None:
        remaining_true_polls["n"] -= 1

    applied = []
    adapter = _make_adapter(tmp_path, on_applied=lambda: applied.append(True), sleep_fn=fake_sleep)

    job = adapter.submit_apply(object(), has_running_job=has_running_job)
    _wait_until_finished(adapter, job.id)

    assert applied == [True]
    assert remaining_true_polls["n"] == 0


def test_apply_update_error_marks_job_failed_with_its_own_message(tmp_path):
    from ash_captions.app.updater import UpdateApplyError

    def failing_apply(artifact_path, *, has_running_job):
        raise UpdateApplyError("A caption job is still running. Try again when the queue is clear.")

    adapter = _make_adapter(tmp_path, apply=failing_apply)

    job = adapter.submit_apply(object(), has_running_job=lambda: True)
    _wait_until_finished(adapter, job.id)

    failed = adapter.get_apply_status(job.id)
    assert failed.status == UpdateApplyStatus.FAILED
    assert "still running" in failed.error


def test_get_unknown_apply_job_raises(tmp_path):
    adapter = _make_adapter(tmp_path)
    with pytest.raises(UpdateApplyNotFoundError):
        adapter.get_apply_status("does-not-exist")
