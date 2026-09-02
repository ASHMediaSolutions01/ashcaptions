"""Tests for update_flow.py: an update never applies over a running job
(it waits, unbounded, with the watcher already stopped), and the
post-apply shutdown still exits on every path.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ash_captions.app.__main__ import _acquire_single_instance_lock
from ash_captions.app.update_flow import (
    UPDATE_SHUTDOWN_WATCHDOG_SECONDS,
    apply_update_shutdown,
    apply_update_when_idle,
    shutdown_with_watchdog,
    wait_until_idle,
)
from ash_captions.app.updater import UpdateApplyError, _HELPER_WAIT_DEADLINE_SECONDS


class FakeWorker:
    def __init__(self) -> None:
        self.stop_calls: list[dict] = []

    def stop(self, timeout=5.0) -> None:
        self.stop_calls.append({"timeout": timeout})


class FakeStoppable:
    def __init__(self) -> None:
        self.stopped = False
        self.started = 0

    def stop(self) -> None:
        self.stopped = True

    def start(self) -> None:
        self.started += 1
        self.stopped = False


class TestWaitUntilIdle:
    def test_returns_immediately_when_nothing_is_running(self) -> None:
        sleeps: list[float] = []
        waited = wait_until_idle(lambda: False, sleep_fn=sleeps.append)
        assert sleeps == []
        assert waited >= 0

    def test_polls_every_30s_with_no_deadline_and_logs_every_5_minutes(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An hour-long job keeps the update waiting for the whole hour."""
        polls = {"left": 130}  # 130 x 30 s = 65 minutes of waiting
        clock = {"now": 0.0}

        def has_running_job() -> bool:
            return polls["left"] > 0

        def fake_sleep(seconds: float) -> None:
            assert seconds == 30.0
            polls["left"] -= 1
            clock["now"] += seconds

        with caplog.at_level("INFO", logger="ash_captions.app"):
            wait_until_idle(has_running_job, sleep_fn=fake_sleep, clock=lambda: clock["now"])

        still_waiting = [r for r in caplog.records if "Still waiting" in r.getMessage()]
        assert len(still_waiting) == 13  # once per 5 min over 65 min
        assert "65 min" in still_waiting[-1].getMessage()


class TestApplyUpdateWhenIdle:
    def test_stops_the_watcher_before_waiting_and_applying(self, tmp_path: Path) -> None:
        watcher = FakeStoppable()
        order: list[str] = []
        running = {"value": True}

        def has_running_job() -> bool:
            order.append(f"check(watcher_stopped={watcher.stopped})")
            return running["value"]

        def fake_sleep(_s: float) -> None:
            running["value"] = False

        def fake_apply(artifact_path, *, has_running_job) -> None:
            order.append("apply")
            assert watcher.stopped is True

        apply_update_when_idle(
            tmp_path / "update.zip",
            has_running_job=has_running_job,
            watcher=watcher,
            sleep_fn=fake_sleep,
            apply=fake_apply,
        )

        assert order[0] == "check(watcher_stopped=True)"
        assert order[-1] == "apply"
        assert watcher.started == 0  # success: the process is about to exit

    def test_restarts_the_watcher_if_apply_refuses(self, tmp_path: Path) -> None:
        watcher = FakeStoppable()

        def refusing_apply(artifact_path, *, has_running_job) -> None:
            raise UpdateApplyError("nope")

        with pytest.raises(UpdateApplyError):
            apply_update_when_idle(
                tmp_path / "update.zip",
                has_running_job=lambda: False,
                watcher=watcher,
                apply=refusing_apply,
            )

        assert watcher.started == 1


class TestDeadlines:
    def test_helper_and_watchdog_deadlines_cover_hour_long_jobs(self) -> None:
        assert _HELPER_WAIT_DEADLINE_SECONDS == 6 * 3600
        assert UPDATE_SHUTDOWN_WATCHDOG_SECONDS == 6 * 3600


class TestApplyUpdateShutdown:
    """The piece that closes the race apply_update()'s has_running_job
    guard narrows but can't fully close on its own: a job that slips past
    the guard must still finish for real before the process exits and the
    detached helper's robocopy proceeds.
    """

    def test_uses_the_unbounded_stop_not_the_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("ash_captions.app.update_flow.time.sleep", lambda _s: None)
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", exits.append)

        worker = FakeWorker()
        watcher = FakeStoppable()
        sweeper = FakeStoppable()
        lock = _acquire_single_instance_lock(tmp_path / "lock")

        apply_update_shutdown(worker, watcher, sweeper, lock)

        assert worker.stop_calls == [{"timeout": None}]
        assert watcher.stopped is True
        assert sweeper.stopped is True
        assert exits == [0]

    def test_waits_for_a_running_job_before_stopping_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ash_captions.app.update_flow.time.sleep", lambda _s: None)
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", lambda code: None)
        worker = FakeWorker()
        polls = {"left": 3}

        def has_running_job() -> bool:
            assert worker.stop_calls == []  # nothing stopped while a job runs
            return polls["left"] > 0

        def fake_sleep(_s: float) -> None:
            polls["left"] -= 1

        apply_update_shutdown(
            worker, FakeStoppable(), FakeStoppable(), None,
            has_running_job=has_running_job, sleep_fn=fake_sleep,
        )

        assert polls["left"] == 0
        assert worker.stop_calls == [{"timeout": None}]

    def test_releases_the_single_instance_lock(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("ash_captions.app.update_flow.time.sleep", lambda _s: None)
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", lambda code: None)

        lock_path = tmp_path / "lock"
        lock = _acquire_single_instance_lock(lock_path)
        assert lock is not None

        apply_update_shutdown(FakeWorker(), FakeStoppable(), FakeStoppable(), lock)

        second = _acquire_single_instance_lock(lock_path)
        assert second is not None
        second.close()

    def test_tolerates_no_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ash_captions.app.update_flow.time.sleep", lambda _s: None)
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", exits.append)

        apply_update_shutdown(FakeWorker(), FakeStoppable(), FakeStoppable(), None)

        assert exits == [0]

    def test_still_exits_if_teardown_itself_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ash_captions.app.update_flow.time.sleep", lambda _s: None)
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", exits.append)

        class ExplodingWorker:
            def stop(self, timeout=5.0) -> None:
                raise RuntimeError("boom")

        apply_update_shutdown(ExplodingWorker(), FakeStoppable(), FakeStoppable(), None)

        assert exits == [0]


class TestShutdownWithWatchdog:
    """POSTMORTEM: an earlier version never cancelled its `threading.Timer`
    and the first test below did not monkeypatch `os._exit`, leaving a live
    timer holding the real `os._exit` to fire mid-suite. `os._exit` is
    patched in every test here, unconditionally, as a mandatory safety net.
    """

    def test_returning_normally_cancels_the_watchdog_timer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", exits.append)

        calls = []
        shutdown_with_watchdog(lambda: calls.append(1), timeout=0.05)
        assert calls == [1]

        time.sleep(0.3)
        assert exits == []

    def test_a_raising_shutdown_fn_still_cancels_the_watchdog_timer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", exits.append)

        def exploding() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            shutdown_with_watchdog(exploding, timeout=0.05)

        time.sleep(0.3)
        assert exits == []

    def test_forces_exit_if_shutdown_fn_never_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exited = threading.Event()
        exits: list[int] = []

        def fake_exit(code: int) -> None:
            exits.append(code)
            exited.set()

        monkeypatch.setattr("ash_captions.app.update_flow.os._exit", fake_exit)

        release = threading.Event()

        def wedged_shutdown() -> None:
            release.wait()

        watchdog_thread = threading.Thread(
            target=shutdown_with_watchdog, args=(wedged_shutdown,), kwargs={"timeout": 0.05}
        )
        watchdog_thread.start()

        assert exited.wait(timeout=2), "watchdog did not force-exit on schedule"
        assert exits == [1]

        release.set()
        watchdog_thread.join(timeout=2)
        assert not watchdog_thread.is_alive()
