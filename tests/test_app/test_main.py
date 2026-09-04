"""Tests for __main__.py's pure/constructible pieces: CLI arg parsing, port
probing, the watch-folder submission callback, and that build_application()
wires every component together without starting threads or a real server.
The update-apply shutdown lives in update_flow.py -- see test_update_flow.py.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from ash_captions import engine, styles
from ash_captions.app.__main__ import (
    _acquire_single_instance_lock,
    _enqueue_watch_file,
    _find_open_port,
    _parse_args,
    _queued_watch_paths,
    _validate_default_preset,
    _warn_already_running,
    build_application,
)
from ash_captions.app.adapter import QueueAdapter
from ash_captions.app.runner import build_run_job
from ash_captions.config import Settings
from ash_captions.pipeline.db import JobStatus as PipelineJobStatus
from ash_captions.pipeline.db import JobStore
from ash_captions.web.models import JobStatus

from .test_runner import FakeTranscriber, _result, run_to_completion


class TestParseArgs:
    def test_bare_invocation_does_not_open_the_browser(self) -> None:
        assert _parse_args([]).open is False

    def test_open_flag_requests_the_browser(self) -> None:
        assert _parse_args(["--open"]).open is True


class TestSingleInstanceLock:
    def test_first_caller_acquires_the_lock(self, tmp_path: Path) -> None:
        handle = _acquire_single_instance_lock(tmp_path / "ash-captions.lock")
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
            assert _acquire_single_instance_lock(lock_path) is None
        finally:
            first.close()

    def test_lock_becomes_available_again_once_released(self, tmp_path: Path) -> None:
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

    def test_permission_error_propagates_for_main_to_explain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args, **kwargs):
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr("builtins.open", refuse)
        with pytest.raises(PermissionError):
            _acquire_single_instance_lock(tmp_path / "ash-captions.lock")


class TestWarnAlreadyRunning:
    def test_shows_a_message_box_and_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(
            "ctypes.windll.user32.MessageBoxW", lambda *args: calls.append(args) or 1, raising=False
        )
        _warn_already_running()
        assert len(calls) == 1
        assert "already running" in calls[0][1]

    def test_falls_back_to_a_log_message_if_the_message_box_itself_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args):
            raise OSError("no user32 in this environment")

        monkeypatch.setattr("ctypes.windll.user32.MessageBoxW", boom, raising=False)
        _warn_already_running()


class TestMainTopLevelHandling:
    def test_a_crashing_run_is_logged_and_shown_then_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Windowed build: the exe must not just vanish."""
        import ash_captions.app.__main__ as main_module

        monkeypatch.setenv("ASH_CAPTIONS_ROOT", str(tmp_path))
        boxes: list[str] = []
        monkeypatch.setattr(main_module, "_message_box", lambda text, **kw: boxes.append(text))
        monkeypatch.setattr(main_module, "configure_logging", lambda path: None)

        def explode(args, settings):
            raise RuntimeError("No free port found in range 8756-8775.")

        monkeypatch.setattr(main_module, "_run", explode)

        with pytest.raises(SystemExit) as raised:
            main_module.main([])

        assert raised.value.code == 1
        assert len(boxes) == 1 and "log file" in boxes[0]

    def test_settings_warnings_are_logged_after_logging_is_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import ash_captions.app.__main__ as main_module

        monkeypatch.setenv("ASH_CAPTIONS_ROOT", str(tmp_path))
        (tmp_path / "settings.json").write_text(json.dumps({"port": "eighty"}), encoding="utf-8")
        monkeypatch.setattr(main_module, "configure_logging", lambda path: None)
        monkeypatch.setattr(main_module, "_run", lambda args, settings: None)

        with caplog.at_level("WARNING", logger="ash_captions.app"):
            main_module.main([])

        assert any("port" in r.getMessage() for r in caplog.records)


class TestUpdateCheckGate:
    def test_skips_the_check_when_the_web_layer_says_updates_are_unsupported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ash_captions.app.__main__ as main_module

        started: list[object] = []
        monkeypatch.setattr(main_module, "check_for_update_in_background", lambda version, state, **kw: started.append(state))
        monkeypatch.setattr(main_module, "updates_supported", lambda: False)
        main_module._start_update_check(main_module.UpdateState())
        assert started == []

        monkeypatch.setattr(main_module, "updates_supported", lambda: True)
        main_module._start_update_check(main_module.UpdateState())
        assert len(started) == 1

    def test_checks_when_the_web_layer_has_no_opinion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ash_captions.app.__main__ as main_module

        started: list[object] = []
        monkeypatch.setattr(main_module, "check_for_update_in_background", lambda version, state, **kw: started.append(state))
        monkeypatch.setattr(main_module, "updates_supported", None)
        main_module._start_update_check(main_module.UpdateState())
        assert len(started) == 1


class TestValidateDefaultPreset:
    def test_a_real_shipped_style_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING", logger="ash_captions.app"):
            _validate_default_preset(Settings(default_preset="POP"))
        assert caplog.records == []

    def test_an_unknown_style_name_logs_a_clear_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING", logger="ash_captions.app"):
            _validate_default_preset(Settings(default_preset="TOTALLY-MADE-UP-STYLE"))
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
        upload_dir=tmp_path / "uploads",
        tmp_dir=tmp_path / "tmp",
        port=0,
        min_free_disk_gb=0,
    )


class TestFindOpenPort:
    def test_returns_the_preferred_port_when_free(self) -> None:
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
        adapter = QueueAdapter(JobStore(settings.db_path), out_dir=settings.out_dir)

        def explode(*args, **kwargs):
            raise RuntimeError("disk fell over")

        monkeypatch.setattr(adapter, "submit", explode)
        _enqueue_watch_file(adapter, settings, settings.in_dir / "clip.mp4")

    def test_default_preset_resolves_through_the_real_style_system_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spec section 6's "80% path", proven with an isolated style dir so
        the shared, externally mutable ``styles/`` tree never decides this."""
        monkeypatch.setattr(engine, "extract_audio", lambda video_path, output_path, **kw: Path(output_path))

        isolated_shipped = tmp_path / "isolated_shipped_styles"
        isolated_shipped.mkdir()
        (isolated_shipped / "pop.json").write_text(json.dumps({"name": "POP"}), encoding="utf-8")
        isolated_user = tmp_path / "isolated_user_styles"

        real_resolve_style = styles.resolve_style
        monkeypatch.setattr(
            styles, "resolve_style",
            lambda name, **kw: real_resolve_style(name, shipped_dir=isolated_shipped, user_dir=isolated_user),
        )

        settings = make_settings(tmp_path)
        settings.ensure_dirs()
        store = JobStore(settings.db_path)
        adapter = QueueAdapter(store, out_dir=settings.out_dir)
        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"fake video")

        _enqueue_watch_file(adapter, settings, video)

        jobs = store.list_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.options.preset == settings.default_preset

        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["hello", "there", "friend"])))
        run_to_completion(run_job, job)

        ass_content = (Path(job.output_dir) / "clip.ass").read_text(encoding="utf-8")
        assert "Style: POP," in ass_content
        assert "Style: CLEAN," not in ass_content


class TestQueuedWatchPaths:
    def test_lists_non_done_inputs_inside_in_dir_only(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        settings.ensure_dirs()
        store = JobStore(settings.db_path)
        adapter = QueueAdapter(store, out_dir=settings.out_dir)
        from ash_captions.web.models import JobOptions as WebJobOptions

        options = WebJobOptions(language="en", dialect=None, preset="POP", burn_in=False, translate_to_english=False)
        pending = settings.in_dir / "pending.mp4"
        failed = settings.in_dir / "failed.mp4"
        done = settings.in_dir / "done.mp4"
        elsewhere = tmp_path / "footage" / "clip.mp4"
        for video in (pending, failed, done, elsewhere):
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b"x")
        adapter.submit(pending, options)
        failed_id = int(adapter.submit(failed, options).id)
        done_id = int(adapter.submit(done, options).id)
        adapter.submit(elsewhere, options)
        store.mark_running(failed_id)
        store.mark_failed(failed_id, "boom")
        store.mark_running(done_id)
        store.mark_done(done_id)

        assert sorted(p.name for p in _queued_watch_paths(store, settings.in_dir)) == ["failed.mp4", "pending.mp4"]


class TestBuildApplication:
    def test_wires_every_component_without_starting_anything(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)

        app, adapter, worker, watcher, sweeper = build_application(settings)

        assert settings.in_dir.is_dir()
        assert settings.out_dir.is_dir()
        assert settings.upload_dir.is_dir()
        assert isinstance(adapter, QueueAdapter)
        assert app.state.queue is adapter
        assert app.state.catalogue is not None
        assert worker._thread is None
        assert watcher._thread is None
        assert sweeper._thread is None

    def test_uploads_go_to_the_settings_upload_dir(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        app, *_ = build_application(settings)
        assert app.state.incoming_dir == settings.upload_dir

    def test_exposes_health_read_from_the_real_worker_and_watcher(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        app, _adapter, worker, watcher, _sweeper = build_application(settings)

        health = app.state.health()
        assert health["worker_alive"] is False
        assert health["last_watcher_poll"] is None

        watcher.poll_once()
        worker.start()
        try:
            health = app.state.health()
        finally:
            worker.stop(timeout=2.0)
        assert health["worker_alive"] is True
        assert health["last_watcher_poll"] is not None

    def test_sweeps_leftover_scratch_at_build(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        leftover = settings.tmp_dir / "job-9" / "clip.wav"
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(b"x")

        build_application(settings)

        assert not leftover.parent.exists()

    def test_exposes_has_running_job_for_the_apply_route(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        app, adapter, _worker, _watcher, _sweeper = build_application(settings)
        assert app.state.has_running_job() is False

        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"fake")
        from ash_captions.web.models import JobOptions as WebJobOptions

        job = adapter.submit(
            video, WebJobOptions(language="en", dialect=None, preset="POP", burn_in=False, translate_to_english=False)
        )
        assert app.state.has_running_job() is False
        JobStore(settings.db_path).mark_running(int(job.id))
        assert app.state.has_running_job() is True

    def test_watcher_is_seeded_from_the_database_at_start(self, tmp_path: Path) -> None:
        """The restart-duplicates bug, end to end: a file still in in\\ with
        a pending row must not be enqueued a second time by a new watcher."""
        settings = make_settings(tmp_path)
        settings.ensure_dirs()
        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"x" * 10)

        _app, adapter, _worker, watcher, _sweeper = build_application(settings)
        watcher.start()
        try:
            for _ in range(4):
                watcher.poll_once()
        finally:
            watcher.stop(timeout=2.0)
        assert len(adapter.list_jobs()) == 1

        # "Restart": brand-new components over the same DB and folder.
        _app2, adapter2, _worker2, watcher2, _sweeper2 = build_application(settings)
        watcher2.start()
        try:
            for _ in range(4):
                watcher2.poll_once()
        finally:
            watcher2.stop(timeout=2.0)

        live = [j for j in adapter2.list_jobs() if j.status == JobStatus.PENDING]
        assert len(live) == 1
        assert len(JobStore(settings.db_path).list_jobs(status=PipelineJobStatus.PENDING)) == 1

    def test_wires_an_update_applier_that_waits_for_idle_and_shuts_down_gracefully(
        self, tmp_path: Path
    ) -> None:
        settings = make_settings(tmp_path)
        app, *_ = build_application(settings)

        from ash_captions.app import updater
        from ash_captions.web.update_adapter import UpdaterAdapter, _default_on_applied

        applier = app.state.update_applier
        assert isinstance(applier, UpdaterAdapter)
        assert applier._on_applied is not _default_on_applied
        assert applier._apply is not updater.apply_update  # the idle-waiting, watcher-stopping wrapper
