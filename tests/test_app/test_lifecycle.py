"""Tests for lifecycle.py: rotating log setup and output retention (spec
sections 10, 12) -- including the ownership rules that keep the sweep from
deleting anything that is not a finished job's own output folder, and
that a cleanup failure never propagates past `run_once`/`clean_old_outputs`.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ash_captions.app.lifecycle import (
    MARKER_FILENAME,
    RetentionSweeper,
    clean_old_outputs,
    clean_old_uploads,
    configure_logging,
    folder_is_live,
    sweep_tmp_dir,
    write_job_marker,
)
from ash_captions.pipeline.db import JobOptions, JobStore


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


def _set_mtime(path: Path, *, days_ago: int) -> None:
    target = (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (target, target))


def _job_folder(root: Path, name: str, *, days_ago: int, job_id: int = 1) -> Path:
    """An output folder as a finished job leaves it: marker + outputs, aged."""
    folder = root / name
    folder.mkdir()
    (folder / "clip.srt").write_text("x")
    marker = write_job_marker(folder, job_id)
    _set_mtime(marker, days_ago=days_ago)
    _set_mtime(folder, days_ago=days_ago)  # last: creating files bumps the folder
    return folder


class TestCleanOldOutputs:
    def test_removes_marked_folders_older_than_retention_window(self, tmp_path: Path) -> None:
        old = _job_folder(tmp_path, "old_job", days_ago=40)

        removed = clean_old_outputs(tmp_path, retention_days=30)

        assert old in removed
        assert not old.exists()

    def test_keeps_folders_within_retention_window(self, tmp_path: Path) -> None:
        recent = _job_folder(tmp_path, "recent_job", days_ago=5)

        assert clean_old_outputs(tmp_path, retention_days=30) == []
        assert recent.exists()

    def test_never_touches_a_folder_without_our_marker(self, tmp_path: Path) -> None:
        """An editor's own subfolder under out\\ is not ours to delete,
        however old it is."""
        _job_folder(tmp_path, "ours", days_ago=40)  # so the sweep is not refused outright
        theirs = tmp_path / "editors_stuff"
        theirs.mkdir()
        (theirs / "notes.txt").write_text("keep me")
        _set_mtime(theirs, days_ago=400)

        removed = clean_old_outputs(tmp_path, retention_days=30)

        assert theirs.exists()
        assert theirs not in removed

    def test_refuses_entirely_when_no_folder_carries_a_marker(self, tmp_path: Path) -> None:
        """out_dir pointed at the wrong place (a footage drive, say) must
        delete nothing, not everything."""
        stray = tmp_path / "footage"
        stray.mkdir()
        _set_mtime(stray, days_ago=400)

        assert clean_old_outputs(tmp_path, retention_days=30) == []
        assert stray.exists()

    def test_refuses_a_drive_root(self, tmp_path: Path) -> None:
        drive_root = Path(tmp_path.anchor)
        assert clean_old_outputs(drive_root, retention_days=30) == []

    def test_a_rerun_into_an_old_folder_is_kept_because_the_marker_is_fresh(
        self, tmp_path: Path
    ) -> None:
        """NTFS doesn't bump a folder's mtime when a file inside is
        rewritten -- the marker's mtime is the one a re-run refreshes."""
        folder = _job_folder(tmp_path, "rerun", days_ago=40)
        write_job_marker(folder, job_id=7)  # a new job just started here
        _set_mtime(folder, days_ago=40)  # folder mtime still looks ancient

        assert clean_old_outputs(tmp_path, retention_days=30) == []
        assert folder.exists()

    def test_skips_a_folder_whose_job_is_still_live(self, tmp_path: Path) -> None:
        busy = _job_folder(tmp_path, "busy", days_ago=40)
        idle = _job_folder(tmp_path, "idle", days_ago=40)

        removed = clean_old_outputs(
            tmp_path, retention_days=30, folder_is_live=lambda folder: folder == busy
        )

        assert busy.exists()
        assert removed == [idle]

    def test_ignores_plain_files_at_the_top_level(self, tmp_path: Path) -> None:
        _job_folder(tmp_path, "ours", days_ago=1)
        stray_file = tmp_path / "not_a_job_folder.txt"
        stray_file.write_text("x")
        _set_mtime(stray_file, days_ago=90)

        assert clean_old_outputs(tmp_path, retention_days=30) == []
        assert stray_file.exists()

    def test_zero_or_negative_retention_days_is_a_no_op(self, tmp_path: Path) -> None:
        old = _job_folder(tmp_path, "old_job", days_ago=999)

        assert clean_old_outputs(tmp_path, retention_days=0) == []
        assert old.exists()

    def test_missing_out_dir_returns_empty_without_raising(self, tmp_path: Path) -> None:
        assert clean_old_outputs(tmp_path / "does-not-exist", retention_days=30) == []

    def test_a_folder_that_cannot_be_removed_does_not_abort_the_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stubborn = _job_folder(tmp_path, "stubborn_job", days_ago=40)
        removable = _job_folder(tmp_path, "removable_job", days_ago=40)

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


class TestFolderIsLive:
    def _options(self) -> JobOptions:
        return JobOptions(language="en", dialect=None, preset="POP", burn=False, translate=False)

    def test_true_for_a_pending_jobs_output_dir_and_input_folder(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.db")
        out = tmp_path / "out" / "clip"
        upload = tmp_path / "uploads" / "abc"
        upload.mkdir(parents=True)
        store.insert_job(upload / "clip.mp4", out, self._options())

        assert folder_is_live(store, out) is True
        assert folder_is_live(store, upload) is True
        assert folder_is_live(store, tmp_path / "out" / "other") is False

    def test_false_once_the_job_is_done(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.db")
        out = tmp_path / "out" / "clip"
        job_id = store.insert_job(tmp_path / "clip.mp4", out, self._options())
        store.mark_running(job_id)
        store.mark_done(job_id)

        assert folder_is_live(store, out) is False


class TestCleanOldUploads:
    def test_removes_old_upload_folders_not_referenced_by_a_live_job(self, tmp_path: Path) -> None:
        old = tmp_path / "aaaa"
        old.mkdir()
        (old / "clip.mp4").write_bytes(b"x")
        _set_mtime(old, days_ago=3)
        fresh = tmp_path / "bbbb"
        fresh.mkdir()

        removed = clean_old_uploads(tmp_path, max_age_days=1)

        assert removed == [old]
        assert fresh.exists()

    def test_skips_a_live_jobs_upload(self, tmp_path: Path) -> None:
        live = tmp_path / "aaaa"
        live.mkdir()
        _set_mtime(live, days_ago=3)

        assert clean_old_uploads(tmp_path, max_age_days=1, folder_is_live=lambda f: f == live) == []
        assert live.exists()


class TestSweepTmpDir:
    def test_removes_everything_inside(self, tmp_path: Path) -> None:
        (tmp_path / "job-3").mkdir()
        (tmp_path / "job-3" / "clip.wav").write_bytes(b"x")
        (tmp_path / "stray.wav").write_bytes(b"x")

        assert sweep_tmp_dir(tmp_path) == 2
        assert list(tmp_path.iterdir()) == []

    def test_missing_dir_is_fine(self, tmp_path: Path) -> None:
        assert sweep_tmp_dir(tmp_path / "nope") == 0


class TestRetentionSweeper:
    def test_run_once_removes_old_folders_and_returns_them(self, tmp_path: Path) -> None:
        old = _job_folder(tmp_path, "old_job", days_ago=40)

        sweeper = RetentionSweeper(tmp_path, retention_days=30)
        removed = sweeper.run_once()

        assert removed == [old]

    def test_run_once_sweeps_uploads_too_when_given_an_upload_dir(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        uploads = tmp_path / "uploads"
        old_upload = uploads / "aaaa"
        old_upload.mkdir(parents=True)
        _set_mtime(old_upload, days_ago=3)

        sweeper = RetentionSweeper(out_dir, retention_days=30, upload_dir=uploads)

        assert sweeper.run_once() == [old_upload]

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
        sweeper = RetentionSweeper(tmp_path, retention_days=30, interval_seconds=0.01)
        sweeper.run_once = lambda: calls.append(1) or []  # type: ignore[method-assign]

        sweeper.start()
        time.sleep(0.1)
        sweeper.stop(timeout=2.0)

        assert len(calls) >= 1

    def test_rejects_non_positive_interval(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            RetentionSweeper(tmp_path, retention_days=30, interval_seconds=0)

    def test_marker_filename_is_dot_prefixed(self) -> None:
        assert MARKER_FILENAME.startswith(".")
