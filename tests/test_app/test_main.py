"""Tests for __main__.py's pure/constructible pieces: CLI arg parsing, port
probing, the watch-folder submission callback, and that build_application()
wires every component together without starting threads or a real server
(that part is exercised by hand / on a real machine, not in the unit suite).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from ash_captions import engine, styles
from ash_captions.app.__main__ import (
    _acquire_single_instance_lock,
    _apply_update_shutdown,
    _enqueue_watch_file,
    _find_open_port,
    _parse_args,
    _shutdown_with_watchdog,
    _validate_default_preset,
    _warn_already_running,
    build_application,
)
from ash_captions.app.adapter import QueueAdapter
from ash_captions.app.runner import build_run_job
from ash_captions.config import Settings
from ash_captions.pipeline.db import JobStore
from ash_captions.web.models import JobStatus

from .test_runner import FakeTranscriber, _result


class TestParseArgs:
    def test_bare_invocation_does_not_open_the_browser(self) -> None:
        """The Task Scheduler "run at logon" entry launches with no
        arguments -- that must stay tray-only, never popping a browser tab
        on every boot."""
        assert _parse_args([]).open is False

    def test_open_flag_requests_the_browser(self) -> None:
        """The desktop shortcut / Start Menu entry passes --open."""
        assert _parse_args(["--open"]).open is True


class TestSingleInstanceLock:
    """A second real process launched while the first is still running
    must not silently race it (two watchers on the same folder, two
    workers polling the same DB) -- see _acquire_single_instance_lock's
    docstring. The lock is OS-enforced (msvcrt byte-range lock), so a
    second `open()` on the same path within *this* test process already
    proves the exclusion; it's the same mechanism a second OS process
    would hit.
    """

    def test_first_caller_acquires_the_lock(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "ash-captions.lock"
        handle = _acquire_single_instance_lock(lock_path)
        try:
            assert handle is not None
        finally:
            if handle is not None:
                handle.close()

    def test_second_caller_is_refused_while_the_first_holds_it(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "ash-captions.lock"
        first = _acquire_single_instance_lock(lock_path)
        assert first is not None
        try:
            second = _acquire_single_instance_lock(lock_path)
            assert second is None
        finally:
            first.close()

    def test_lock_becomes_available_again_once_released(self, tmp_path: Path) -> None:
        """Simulates a crashed prior instance: closing the handle (which is
        also what happens when a process dies, cleanly or not -- Windows
        releases the lock either way) must let the next launch succeed
        without any manual cleanup.
        """
        lock_path = tmp_path / "ash-captions.lock"
        first = _acquire_single_instance_lock(lock_path)
        assert first is not None
        first.close()

        second = _acquire_single_instance_lock(lock_path)
        try:
            assert second is not None
        finally:
            if second is not None:
                second.close()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "nested" / "dir" / "ash-captions.lock"
        handle = _acquire_single_instance_lock(lock_path)
        try:
            assert handle is not None
            assert lock_path.parent.is_dir()
        finally:
            if handle is not None:
                handle.close()


class TestWarnAlreadyRunning:
    def test_shows_a_message_box_and_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(
            "ctypes.windll.user32.MessageBoxW",
            lambda *args: calls.append(args) or 1,
            raising=False,
        )

        _warn_already_running()  # must not raise, must not actually block on a real dialog

        assert len(calls) == 1
        assert "already running" in calls[0][1]

    def test_falls_back_to_a_log_message_if_the_message_box_itself_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args):
            raise OSError("no user32 in this environment")

        monkeypatch.setattr("ctypes.windll.user32.MessageBoxW", boom, raising=False)

        _warn_already_running()  # must not raise even if the message box itself fails


class TestValidateDefaultPreset:
    """Diagnostic-only startup check: a bad `default_preset` must never
    block startup (resolve_style() already handles that at job time), but
    it should be logged loudly rather than silently degrading every
    watch-folder job to the default style unnoticed.
    """

    def test_a_real_shipped_style_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = Settings(default_preset="POP")
        with caplog.at_level("WARNING", logger="ash_captions.app"):
            _validate_default_preset(settings)
        assert caplog.records == []

    def test_an_unknown_style_name_logs_a_clear_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = Settings(default_preset="TOTALLY-MADE-UP-STYLE")
        with caplog.at_level("WARNING", logger="ash_captions.app"):
            _validate_default_preset(settings)

        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert "TOTALLY-MADE-UP-STYLE" in message
        assert styles.DEFAULT_STYLE.name in message


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        in_dir=tmp_path / "in",
        out_dir=tmp_path / "out",
        db_path=tmp_path / "jobs.db",
        log_path=tmp_path / "log.txt",
        glossary_dir=tmp_path / "glossaries",
        port=0,  # overridden per-test where relevant
    )


class TestFindOpenPort:
    def test_returns_the_preferred_port_when_free(self) -> None:
        # Bind and release to get a genuinely free ephemeral port to probe for.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]

        assert _find_open_port(free_port, max_probes=1) == free_port

    def test_probes_upward_when_preferred_port_is_taken(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            taken_port = held.getsockname()[1]

            found = _find_open_port(taken_port, max_probes=5)

            assert found != taken_port
            assert taken_port < found <= taken_port + 4

    def test_raises_when_no_port_is_free_in_range(self) -> None:
        sockets = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                base_port = probe.getsockname()[1]

            for offset in range(3):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", base_port + offset))
                s.listen(1)
                sockets.append(s)

            with pytest.raises(RuntimeError):
                _find_open_port(base_port, max_probes=3)
        finally:
            for s in sockets:
                s.close()


class TestEnqueueWatchFile:
    def test_submits_a_job_using_configured_defaults(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        settings.ensure_dirs()
        from ash_captions.pipeline.db import JobStore

        adapter = QueueAdapter(JobStore(settings.db_path), out_dir=settings.out_dir)
        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"fake")

        _enqueue_watch_file(adapter, settings, video)

        jobs = adapter.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.PENDING
        assert jobs[0].options.language == settings.default_language
        assert jobs[0].options.preset == settings.default_preset

    def test_a_bad_drop_is_logged_not_raised(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = make_settings(tmp_path)
        settings.ensure_dirs()
        from ash_captions.pipeline.db import JobStore

        adapter = QueueAdapter(JobStore(settings.db_path), out_dir=settings.out_dir)

        def explode(*args, **kwargs):
            raise RuntimeError("disk fell over")

        monkeypatch.setattr(adapter, "submit", explode)

        # Must not raise -- a bad drop must never kill the watcher thread.
        _enqueue_watch_file(adapter, settings, settings.in_dir / "clip.mp4")

    def test_default_preset_resolves_through_the_real_style_system_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spec section 6's "80% path": a file dropped in `in\\` with zero
        UI interaction must get the *configured default* style, resolved
        through the same real ``styles.resolve_style()`` a browser
        submission uses -- not a style hardcoded in this test, and not a
        silent fallback because the wiring between ``_enqueue_watch_file``
        and ``runner.build_run_job`` broke. Most jobs never touch the API
        at all, so if this path is broken, most jobs are broken.

        Hermetic on purpose: this repo's real ``styles/`` directory (and
        the real, machine-wide ``C:\\AshCaptions\\styles`` user-style
        directory ``resolve_style()`` also checks) are shared, externally
        mutable state -- in this project's live, multi-agent workspace,
        other agents edit files in this same tree while tests run
        elsewhere. A test's pass/fail must never hinge on that, so this
        points ``resolve_style()`` at an isolated tmp directory holding a
        self-contained, minimal ``"POP"`` style (every field but ``name``
        has a schema default -- see ``styles/schema.py``) for the whole
        test -- proving the *wiring* end to end without reading the real,
        shared style files at all.
        """
        # No real ffmpeg needed -- FakeTranscriber never reads the audio
        # file, extraction just needs to not blow up.
        monkeypatch.setattr(engine, "extract_audio", lambda video_path, output_path, **kw: Path(output_path))

        isolated_shipped = tmp_path / "isolated_shipped_styles"
        isolated_shipped.mkdir()
        (isolated_shipped / "pop.json").write_text(json.dumps({"name": "POP"}), encoding="utf-8")
        isolated_user = tmp_path / "isolated_user_styles"  # left non-existent: no user overrides

        real_resolve_style = styles.resolve_style
        monkeypatch.setattr(
            styles,
            "resolve_style",
            lambda name, **kw: real_resolve_style(name, shipped_dir=isolated_shipped, user_dir=isolated_user),
        )

        settings = make_settings(tmp_path)
        settings.ensure_dirs()
        store = JobStore(settings.db_path)
        adapter = QueueAdapter(store, out_dir=settings.out_dir)

        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"fake video")

        # Exactly what pipeline.Watcher's on_ready callback does for a
        # file that just stabilised.
        _enqueue_watch_file(adapter, settings, video)

        jobs = store.list_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.options.preset == settings.default_preset

        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)
        run_job(job, lambda _p: None)

        ass_content = (Path(job.output_dir) / "clip.ass").read_text(encoding="utf-8")
        # "POP" (the ASS-safe name has no spaces/commas to strip) must
        # appear in the Style line. If resolve_style() ever silently fell
        # back (a typo'd or renamed default_preset, or a wiring break
        # between _enqueue_watch_file and runner.build_run_job), this
        # would say DEFAULT_STYLE.name ("CLEAN") instead -- caught here,
        # not in a client's delivery.
        assert "Style: POP," in ass_content
        assert "Style: CLEAN," not in ass_content


class TestBuildApplication:
    def test_wires_every_component_without_starting_anything(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)

        app, adapter, worker, watcher, sweeper = build_application(settings)

        assert settings.in_dir.is_dir()
        assert settings.out_dir.is_dir()
        assert isinstance(adapter, QueueAdapter)
        assert app.state.queue is adapter
        assert app.state.catalogue is not None
        # None of these have background threads running yet.
        assert worker._thread is None
        assert watcher._thread is None
        assert sweeper._thread is None

    def test_exposes_has_running_job_for_the_future_apply_route(self, tmp_path: Path) -> None:
        """The correct has_running_job recipe for updater.apply_update(),
        defined once here and exposed on app.state so whoever wires the
        actual apply endpoint doesn't reimplement it."""
        settings = make_settings(tmp_path)
        app, adapter, _worker, _watcher, _sweeper = build_application(settings)

        assert app.state.has_running_job() is False

        video = settings.in_dir / "clip.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"fake")
        from ash_captions.web.models import JobOptions as WebJobOptions

        job = adapter.submit(
            video,
            WebJobOptions(language="en", dialect=None, preset="POP", burn_in=False, translate_to_english=False),
        )
        assert app.state.has_running_job() is False  # still just pending, not running

        # Reach into the real store the same way JobWorker does to mark it running.
        from ash_captions.pipeline.db import JobStore

        JobStore(settings.db_path).mark_running(int(job.id))
        assert app.state.has_running_job() is True

    def test_wires_an_update_applier_whose_on_applied_is_the_graceful_shutdown(
        self, tmp_path: Path
    ) -> None:
        """create_app() must receive a real UpdaterAdapter, not the crude
        os._exit(0) default -- that default skips worker.stop() entirely,
        which is exactly the race this whole feature exists to close."""
        settings = make_settings(tmp_path)
        app, _adapter, worker, watcher, sweeper = build_application(settings)

        from ash_captions.web.update_adapter import UpdaterAdapter

        assert isinstance(app.state.update_applier, UpdaterAdapter)
        # Not the module's own crude default -- a real closure over this
        # app's actual worker/watcher/sweeper.
        from ash_captions.web.update_adapter import _default_on_applied

        assert app.state.update_applier._on_applied is not _default_on_applied


class TestApplyUpdateShutdown:
    """The piece that closes the race apply_update()'s has_running_job
    guard narrows but can't fully close on its own (team-lead's own
    framing): a job that slips past the guard must still finish for real
    before the process exits and the detached helper's robocopy proceeds.
    """

    class FakeWorker:
        def __init__(self) -> None:
            self.stop_calls: list[dict] = []

        def stop(self, timeout=5.0) -> None:
            self.stop_calls.append({"timeout": timeout})

    class FakeStoppable:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    def test_uses_the_unbounded_stop_not_the_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The exact thing team-lead asked to see proven: this path must
        call worker.stop(timeout=None), never JobWorker's normal 5s
        default -- a test with no job actually running would pass either
        way, so this asserts on the *argument*, not just "didn't crash".
        """
        monkeypatch.setattr("ash_captions.app.__main__.time.sleep", lambda _s: None)
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.__main__.os._exit", exits.append)

        worker = self.FakeWorker()
        watcher = self.FakeStoppable()
        sweeper = self.FakeStoppable()
        lock_path = tmp_path / "lock"
        lock = _acquire_single_instance_lock(lock_path)

        _apply_update_shutdown(worker, watcher, sweeper, lock)

        assert worker.stop_calls == [{"timeout": None}]
        assert watcher.stopped is True
        assert sweeper.stopped is True
        assert exits == [0]

    def test_releases_the_single_instance_lock(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("ash_captions.app.__main__.time.sleep", lambda _s: None)
        monkeypatch.setattr("ash_captions.app.__main__.os._exit", lambda code: None)

        lock_path = tmp_path / "lock"
        lock = _acquire_single_instance_lock(lock_path)
        assert lock is not None

        _apply_update_shutdown(self.FakeWorker(), self.FakeStoppable(), self.FakeStoppable(), lock)

        # Released, not just closed from this handle's own point of view --
        # a second acquire on the same path must now succeed.
        second = _acquire_single_instance_lock(lock_path)
        assert second is not None
        second.close()

    def test_tolerates_no_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ash_captions.app.__main__.time.sleep", lambda _s: None)
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.__main__.os._exit", exits.append)

        _apply_update_shutdown(self.FakeWorker(), self.FakeStoppable(), self.FakeStoppable(), None)

        assert exits == [0]

    def test_still_exits_if_teardown_itself_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An update that leaves the app half-shut-down and neither
        finishing the apply nor able to start again is the worst outcome
        -- teardown failing must not skip the exit."""
        monkeypatch.setattr("ash_captions.app.__main__.time.sleep", lambda _s: None)
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.__main__.os._exit", exits.append)

        class ExplodingWorker:
            def stop(self, timeout=5.0) -> None:
                raise RuntimeError("boom")

        _apply_update_shutdown(ExplodingWorker(), self.FakeStoppable(), self.FakeStoppable(), None)

        assert exits == [0]


class TestShutdownWithWatchdog:
    """POSTMORTEM: an earlier version of `_shutdown_with_watchdog` never
    cancelled its `threading.Timer`, and the first test below did not
    monkeypatch `os._exit`. That left a real, armed 5-second timer holding
    the real `os._exit` running after the test returned "green" -- which
    fired mid-suite, in whatever unrelated test happened to be running
    five seconds later, and silently killed the entire pytest process
    with no traceback, no summary, and a different apparent "stopping
    point" every run depending purely on wall-clock timing. `test_app` run
    alone "passed" only because that whole run finished in under 5
    seconds -- by luck, not correctness. Both fixed below: the function
    now cancels its timer in a `finally`, and this test asserts that
    directly (waiting past the timeout and checking `os._exit` was never
    called) instead of merely checking the call returned, which would
    pass whether or not the bug existed. `os._exit` is patched in every
    test in this class now, unconditionally, as a mandatory safety net --
    no test here may ever hold a live timer over the real one again.
    """

    def test_returning_normally_cancels_the_watchdog_timer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.__main__.os._exit", exits.append)

        calls = []
        _shutdown_with_watchdog(lambda: calls.append(1), timeout=0.05)
        assert calls == [1]

        # If the timer were still armed, it would have fired well within
        # this wait -- proof of cancellation, not just "the call returned".
        time.sleep(0.3)
        assert exits == []

    def test_a_raising_shutdown_fn_still_cancels_the_watchdog_timer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cancel lives in a `finally` specifically so an exception
        from shutdown_fn can't leave the timer armed either."""
        exits: list[int] = []
        monkeypatch.setattr("ash_captions.app.__main__.os._exit", exits.append)

        def exploding() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            _shutdown_with_watchdog(exploding, timeout=0.05)

        time.sleep(0.3)
        assert exits == []

    def test_forces_exit_if_shutdown_fn_never_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulates a genuinely wedged job: shutdown_fn blocks forever.
        The watchdog's own forced-exit path must still fire on schedule.
        """
        exited = threading.Event()
        exits: list[int] = []

        def fake_exit(code: int) -> None:
            exits.append(code)
            exited.set()

        monkeypatch.setattr("ash_captions.app.__main__.os._exit", fake_exit)

        release = threading.Event()

        def wedged_shutdown() -> None:
            release.wait()  # never returns until this test releases it, below

        watchdog_thread = threading.Thread(
            target=_shutdown_with_watchdog, args=(wedged_shutdown,), kwargs={"timeout": 0.05}
        )
        watchdog_thread.start()

        assert exited.wait(timeout=2), "watchdog did not force-exit on schedule"
        assert exits == [1]  # the watchdog's forced-exit code, distinct from the graceful path's 0

        release.set()  # let wedged_shutdown return so the background thread can end cleanly
        watchdog_thread.join(timeout=2)
        assert not watchdog_thread.is_alive()
