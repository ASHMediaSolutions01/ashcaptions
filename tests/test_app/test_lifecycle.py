"""Tests for lifecycle.py: rotating log setup and 30-day output retention
(spec sections 10, 12) -- including that a cleanup failure never
propagates past `run_once`/`clean_old_outputs`.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ash_captions.app.lifecycle import RetentionSweeper, clean_old_outputs, configure_logging


class TestConfigureLogging:
    def test_writes_to_the_configured_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "ash-captions.log"
        logger = configure_logging(log_path)
        try:
            logger.info("hello from the test suite")
            for handler in logging.getLogger().handlers:
                handler.flush()
            assert log_path.is_file()
            assert "hello from the test suite" in log_path.read_text(encoding="utf-8")
        finally:
            _remove_handlers_for(log_path)

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        log_path = tmp_path / "nested" / "dir" / "ash-captions.log"
        configure_logging(log_path)
        try:
            assert log_path.parent.is_dir()
        finally:
            _remove_handlers_for(log_path)


def _remove_handlers_for(log_path: Path) -> None:
    """Close and detach handlers pointed at `log_path` so the RotatingFileHandler
    releases its file lock -- otherwise Windows can't clean up `tmp_path`.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        base_filename = getattr(handler, "baseFilename", None)
        if base_filename and Path(base_filename) == log_path:
            handler.close()
            root.removeHandler(handler)


class TestCleanOldOutputs:
    def test_removes_folders_older_than_retention_window(self, tmp_path: Path) -> None:
        old = tmp_path / "old_job"
        old.mkdir()
        (old / "clip.srt").write_text("x")
        _set_mtime(old, days_ago=40)

        removed = clean_old_outputs(tmp_path, retention_days=30)

        assert old in removed
        assert not old.exists()

    def test_keeps_folders_within_retention_window(self, tmp_path: Path) -> None:
        recent = tmp_path / "recent_job"
        recent.mkdir()
        _set_mtime(recent, days_ago=5)

        removed = clean_old_outputs(tmp_path, retention_days=30)

        assert removed == []
        assert recent.exists()

    def test_ignores_plain_files_at_the_top_level(self, tmp_path: Path) -> None:
        stray_file = tmp_path / "not_a_job_folder.txt"
        stray_file.write_text("x")
        _set_mtime(stray_file, days_ago=90)

        removed = clean_old_outputs(tmp_path, retention_days=30)

        assert removed == []
        assert stray_file.exists()

    def test_zero_or_negative_retention_days_is_a_no_op(self, tmp_path: Path) -> None:
        old = tmp_path / "old_job"
        old.mkdir()
        _set_mtime(old, days_ago=999)

        assert clean_old_outputs(tmp_path, retention_days=0) == []
        assert old.exists()

    def test_missing_out_dir_returns_empty_without_raising(self, tmp_path: Path) -> None:
        assert clean_old_outputs(tmp_path / "does-not-exist", retention_days=30) == []

    def test_a_folder_that_cannot_be_removed_does_not_abort_the_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stubborn = tmp_path / "stubborn_job"
        stubborn.mkdir()
        _set_mtime(stubborn, days_ago=40)
        removable = tmp_path / "removable_job"
        removable.mkdir()
        _set_mtime(removable, days_ago=40)

        import shutil

        real_rmtree = shutil.rmtree

        def flaky_rmtree(path, *args, **kwargs):
            if Path(path) == stubborn:
                raise OSError("file in use")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr("ash_captions.app.lifecycle.shutil.rmtree", flaky_rmtree)

        removed = clean_old_outputs(tmp_path, retention_days=30)

        assert stubborn.exists()  # failed removal, but did not raise
        assert removable in removed
        assert not removable.exists()


def _set_mtime(path: Path, *, days_ago: int) -> None:
    target = (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (target, target))


class TestRetentionSweeper:
    def test_run_once_removes_old_folders_and_returns_them(self, tmp_path: Path) -> None:
        old = tmp_path / "old_job"
        old.mkdir()
        _set_mtime(old, days_ago=40)

        sweeper = RetentionSweeper(tmp_path, retention_days=30)
        removed = sweeper.run_once()

        assert removed == [old]

    def test_run_once_never_raises_even_if_clean_old_outputs_blows_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ash_captions.app.lifecycle.clean_old_outputs",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        sweeper = RetentionSweeper(tmp_path, retention_days=30)

        assert sweeper.run_once() == []  # swallowed, not raised

    def test_start_and_stop_run_the_loop_without_real_waiting(self, tmp_path: Path) -> None:
        calls = []
        sweeper = RetentionSweeper(
            tmp_path,
            retention_days=30,
            interval_seconds=0.01,
        )
        sweeper.run_once = lambda: calls.append(1) or []  # type: ignore[method-assign]

        sweeper.start()
        time.sleep(0.1)
        sweeper.stop(timeout=2.0)

        assert len(calls) >= 1

    def test_rejects_non_positive_interval(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            RetentionSweeper(tmp_path, retention_days=30, interval_seconds=0)
