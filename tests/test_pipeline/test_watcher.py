"""Tests for the watch-folder readiness logic (spec section 8.2).

These exercise ``Watcher.poll_once()`` directly, tick by tick, instead of
starting the background thread or the watchdog observer. Each call to
``poll_once()`` stands in for one ~1.5s check; since the stability tracker
only cares about consecutive *ticks*, not wall-clock time, the suite never
sleeps for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.pipeline.watcher import IGNORED_EXTENSIONS, StabilityTracker, Watcher, is_eligible


def touch(path: Path, content: bytes) -> None:
    path.write_bytes(content)


class TestIsEligible:
    def test_accepts_known_video_extensions(self) -> None:
        assert is_eligible(Path("clip.mp4"))
        assert is_eligible(Path("clip.MOV"))
        assert is_eligible(Path("clip.mkv"))
        assert is_eligible(Path("clip.mxf"))

    def test_rejects_non_video_extensions(self) -> None:
        assert not is_eligible(Path("notes.txt"))
        assert not is_eligible(Path("clip.srt"))

    def test_rejects_partial_download_extensions(self) -> None:
        for suffix in IGNORED_EXTENSIONS:
            assert not is_eligible(Path(f"clip.mp4{suffix}"))
        assert not is_eligible(Path("clip.mp4.tmp"))
        assert not is_eligible(Path("clip.mp4.part"))
        assert not is_eligible(Path("clip.mp4.crdownload"))


class TestStabilityTracker:
    def test_not_stable_until_required_matching_observations(self) -> None:
        tracker = StabilityTracker(required_checks=3)
        path = Path("clip.mp4")

        assert tracker.observe(path, 100, 1.0) is False  # 1st: baseline
        assert tracker.observe(path, 100, 1.0) is False  # 2nd: matches
        assert tracker.observe(path, 100, 1.0) is True  # 3rd: matches -> stable

    def test_a_change_resets_the_streak(self) -> None:
        tracker = StabilityTracker(required_checks=3)
        path = Path("clip.mp4")

        assert tracker.observe(path, 100, 1.0) is False
        assert tracker.observe(path, 500, 2.0) is False  # still growing -> reset
        assert tracker.observe(path, 500, 2.0) is False  # 2nd matching
        assert tracker.observe(path, 500, 2.0) is True  # 3rd matching -> stable

    def test_forget_clears_state(self) -> None:
        tracker = StabilityTracker(required_checks=2)
        path = Path("clip.mp4")
        tracker.observe(path, 100, 1.0)
        tracker.forget(path)
        assert tracker.observe(path, 100, 1.0) is False  # streak restarted


class TestWatcherPollOnce:
    def make_watcher(self, watch_dir: Path, **overrides: object) -> tuple[Watcher, list[Path]]:
        ready: list[Path] = []
        defaults: dict[str, object] = dict(stable_checks_required=3)
        defaults.update(overrides)
        watcher = Watcher(watch_dir, on_ready=ready.append, **defaults)
        return watcher, ready

    def test_growing_file_is_not_enqueued(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path)
        clip = tmp_path / "clip.mp4"

        touch(clip, b"a" * 100)
        watcher.poll_once()
        touch(clip, b"a" * 500)  # still copying
        watcher.poll_once()
        touch(clip, b"a" * 900)  # still copying
        watcher.poll_once()

        assert ready == []

    def test_file_is_enqueued_once_size_stabilises(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path)
        clip = tmp_path / "clip.mp4"

        touch(clip, b"a" * 100)
        watcher.poll_once()  # 1st observation of final size
        watcher.poll_once()  # 2nd matching observation
        watcher.poll_once()  # 3rd matching observation -> stable, exclusive-open succeeds

        assert ready == [clip]

    def test_growing_then_stabilising_is_eventually_enqueued(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path)
        clip = tmp_path / "clip.mp4"

        touch(clip, b"a" * 100)
        watcher.poll_once()
        touch(clip, b"a" * 5000)  # still copying, resets the streak
        watcher.poll_once()
        assert ready == []

        watcher.poll_once()  # size now unchanged since previous tick (2nd match)
        assert ready == []
        watcher.poll_once()  # 3rd matching observation -> stable
        assert ready == [clip]

    def test_ready_file_is_not_enqueued_twice(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path)
        clip = tmp_path / "clip.mp4"
        touch(clip, b"a" * 100)

        for _ in range(3):
            watcher.poll_once()
        for _ in range(3):
            watcher.poll_once()

        assert ready == [clip]

    def test_non_video_and_partial_download_files_are_ignored(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path)
        touch(tmp_path / "notes.txt", b"hello")
        touch(tmp_path / "clip.mp4.part", b"a" * 100)
        touch(tmp_path / "clip.mp4.crdownload", b"a" * 100)

        for _ in range(5):
            watcher.poll_once()

        assert ready == []

    def test_file_removed_before_stabilising_is_dropped_silently(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path)
        clip = tmp_path / "clip.mp4"
        touch(clip, b"a" * 100)

        watcher.poll_once()
        clip.unlink()
        watcher.poll_once()
        watcher.poll_once()

        assert ready == []

    def test_exclusive_open_failure_defers_readiness_without_losing_stability(
        self, tmp_path: Path
    ) -> None:
        """A PermissionError-equivalent from the open check must not silently
        drop the file -- it must retry on a later tick instead."""
        locked = {"value": True}

        def flaky_open_check(path: Path) -> bool:
            return not locked["value"]

        watcher, ready = self.make_watcher(tmp_path, open_check=flaky_open_check)
        clip = tmp_path / "clip.mp4"
        touch(clip, b"a" * 100)

        for _ in range(3):
            watcher.poll_once()
        assert ready == []  # stable, but still "locked"

        locked["value"] = False
        watcher.poll_once()  # size/mtime unchanged, handle now free

        assert ready == [clip]

    def test_watch_dir_is_created_if_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "in"
        assert not missing.exists()

        watcher, _ready = self.make_watcher(missing)

        assert missing.is_dir()
        watcher.poll_once()  # does not raise on an empty, freshly-created dir

    def test_custom_stable_checks_required(self, tmp_path: Path) -> None:
        watcher, ready = self.make_watcher(tmp_path, stable_checks_required=1)
        clip = tmp_path / "clip.mp4"
        touch(clip, b"a" * 100)

        watcher.poll_once()

        assert ready == [clip]


class TestWatcherLifecycle:
    def test_start_and_stop_do_not_raise(self, tmp_path: Path) -> None:
        watcher, _ready = TestWatcherPollOnce().make_watcher(
            tmp_path, check_interval=0.01
        )
        watcher.start()
        try:
            assert watcher._thread is not None
            assert watcher._thread.is_alive()
        finally:
            watcher.stop(timeout=2.0)

        assert watcher._thread is None

    def test_start_twice_raises(self, tmp_path: Path) -> None:
        watcher, _ready = TestWatcherPollOnce().make_watcher(
            tmp_path, check_interval=0.01
        )
        watcher.start()
        try:
            with pytest.raises(RuntimeError):
                watcher.start()
        finally:
            watcher.stop(timeout=2.0)
