"""Tests for __main__.py's pure/constructible pieces: CLI arg parsing, port
probing, the watch-folder submission callback, and that build_application()
wires every component together without starting threads or a real server
(that part is exercised by hand / on a real machine, not in the unit suite).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from ash_captions.app.__main__ import _enqueue_watch_file, _find_open_port, _parse_args, build_application
from ash_captions.app.adapter import QueueAdapter
from ash_captions.config import Settings
from ash_captions.web.models import JobStatus


class TestParseArgs:
    def test_bare_invocation_does_not_open_the_browser(self) -> None:
        """The Task Scheduler "run at logon" entry launches with no
        arguments -- that must stay tray-only, never popping a browser tab
        on every boot."""
        assert _parse_args([]).open is False

    def test_open_flag_requests_the_browser(self) -> None:
        """The desktop shortcut / Start Menu entry passes --open."""
        assert _parse_args(["--open"]).open is True


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
