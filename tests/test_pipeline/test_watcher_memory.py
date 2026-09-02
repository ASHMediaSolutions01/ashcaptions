"""Tests for what the watcher remembers: already-queued files seeded from
the database at start, files forgotten once they leave the folder, the
read-only-file open probe, and the lock-wait log lines.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ash_captions.pipeline.watcher import (
    LOCKED_WARNING_AFTER_SECONDS,
    Watcher,
    _default_exclusive_open,
    _probe_share_exclusive,
)


def make_watcher(watch_dir: Path, **overrides):
    ready: list[Path] = []
    kwargs = dict(stable_checks_required=1)
    kwargs.update(overrides)
    return Watcher(watch_dir, on_ready=ready.append, **kwargs), ready


class TestSeeding:
    def test_seeded_paths_are_never_reported(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        watcher, ready = make_watcher(tmp_path)

        assert watcher.seed_enqueued([clip]) == 1
        for _ in range(3):
            watcher.poll_once()

        assert ready == []

    def test_paths_outside_the_watch_dir_are_ignored_when_seeding(self, tmp_path: Path) -> None:
        watcher, _ = make_watcher(tmp_path / "in")
        assert watcher.seed_enqueued([tmp_path / "elsewhere" / "clip.mp4"]) == 0

    def test_start_seeds_from_known_paths(self, tmp_path: Path) -> None:
        """The restart case: the DB still lists the file as pending, so a
        fresh process must not report it again."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        watcher, ready = make_watcher(tmp_path, known_paths=lambda: [clip], check_interval=0.05)

        watcher.start()
        try:
            for _ in range(3):
                watcher.poll_once()
        finally:
            watcher.stop(timeout=2.0)

        assert ready == []
        assert clip in watcher.enqueued_paths()

    def test_a_failing_known_paths_does_not_stop_the_watcher(self, tmp_path: Path) -> None:
        def explode():
            raise RuntimeError("db is on fire")

        watcher, _ = make_watcher(tmp_path, known_paths=explode, check_interval=0.05)
        watcher.start()
        try:
            assert watcher.is_alive()
        finally:
            watcher.stop(timeout=2.0)


class TestForgetting:
    def test_a_file_that_disappears_is_forgotten_so_a_redrop_is_reported_again(
        self, tmp_path: Path
    ) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        watcher, ready = make_watcher(tmp_path)

        watcher.poll_once()
        assert ready == [clip]
        clip.unlink()  # a finished job deleted it
        watcher.poll_once()
        assert watcher.enqueued_paths() == set()

        clip.write_bytes(b"y" * 20)  # the editor drops a new file under the same name
        watcher.poll_once()

        assert ready == [clip, clip]

    def test_an_unlistable_directory_forgets_nothing(self, tmp_path: Path, monkeypatch) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        watcher, ready = make_watcher(tmp_path)
        watcher.poll_once()
        assert ready == [clip]

        real_iterdir = Path.iterdir
        monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(OSError("network gone")))
        watcher.poll_once()
        monkeypatch.setattr(Path, "iterdir", real_iterdir)
        watcher.poll_once()

        assert ready == [clip]  # still remembered: not reported twice

    def test_last_poll_at_is_recorded(self, tmp_path: Path) -> None:
        watcher, _ = make_watcher(tmp_path)
        assert watcher.last_poll_at is None
        watcher.poll_once()
        assert watcher.last_poll_at is not None


@pytest.mark.skipif(os.name != "nt", reason="read-only attribute semantics are Windows-specific")
class TestReadOnlyFiles:
    def test_a_read_only_file_passes_the_open_check(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        os.chmod(clip, stat.S_IREAD)
        try:
            assert _default_exclusive_open(clip) is True
        finally:
            os.chmod(clip, stat.S_IWRITE | stat.S_IREAD)

    def test_a_read_only_file_still_held_open_is_deferred(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        os.chmod(clip, stat.S_IREAD)
        try:
            with open(clip, "rb"):  # somebody else's handle
                assert _default_exclusive_open(clip) is False
        finally:
            os.chmod(clip, stat.S_IWRITE | stat.S_IREAD)

    def test_share_probe_raises_for_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _probe_share_exclusive(tmp_path / "gone.mp4")

    def test_read_only_file_is_enqueued_end_to_end(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        os.chmod(clip, stat.S_IREAD)
        watcher, ready = make_watcher(tmp_path)
        try:
            watcher.poll_once()
            assert ready == [clip]
        finally:
            os.chmod(clip, stat.S_IWRITE | stat.S_IREAD)


class TestTransitionLogs:
    def test_seen_locked_warning_and_enqueued_each_logged_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        clock = {"now": 0.0}
        locked = {"value": True}
        watcher, ready = make_watcher(
            tmp_path, open_check=lambda p: not locked["value"], clock=lambda: clock["now"]
        )

        with caplog.at_level("INFO", logger="ash_captions.pipeline.watcher"):
            watcher.poll_once()  # seen + stable + locked
            clock["now"] += 10
            watcher.poll_once()  # still locked, under a minute
            clock["now"] += LOCKED_WARNING_AFTER_SECONDS
            watcher.poll_once()  # over a minute: one warning
            watcher.poll_once()  # not a second one
            locked["value"] = False
            watcher.poll_once()  # enqueued

        messages = [r.getMessage() for r in caplog.records]
        assert sum("seen clip.mp4" in m for m in messages) == 1
        assert sum("still held open" in m for m in messages) == 1
        assert sum("locked by another program for over" in m for m in messages) == 1
        assert sum("enqueued clip.mp4" in m for m in messages) == 1
        assert ready == [clip]
