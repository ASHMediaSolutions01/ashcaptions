"""Tests for updater.py: the launch-time background check (spec section
11.4 / docs/INSTALL.md's consumption contract) and the explicit-click
download/verify/apply path.

No test touches the network -- every fetch/download is injected. Per the
brief: newer version offered, same version silent, older version silent,
malformed manifest, network failure, and sha256 mismatch on download
rejecting the artifact are all covered explicitly, plus the background
wrapper and the apply hand-off.
"""

from __future__ import annotations

import hashlib
import json
import threading
import zipfile
from pathlib import Path

import pytest

from ash_captions.app import updater


def make_manifest(
    *,
    version: str = "0.2.0",
    sha256: str = "a" * 64,
    size_bytes: int = 100,
    url: str = "https://example.com/AshCaptions-0.2.0-win64.zip",
    filename: str = "AshCaptions-0.2.0-win64.zip",
    notes: str | None = None,
) -> dict:
    manifest = {
        "schema_version": 1,
        "channel": "stable",
        "version": version,
        "build_date": "2026-01-01T00:00:00+00:00",
        "artifact": {"filename": filename, "url": url, "sha256": sha256, "size_bytes": size_bytes},
    }
    if notes is not None:
        manifest["notes"] = notes
    return manifest


def fetch_returning(payload: bytes):
    def _fetch(url: str, timeout: float) -> bytes:
        return payload
    return _fetch


def fetch_raising(exc: Exception):
    def _fetch(url: str, timeout: float) -> bytes:
        raise exc
    return _fetch


class TestCheckForUpdate:
    def test_newer_version_is_offered(self) -> None:
        manifest = make_manifest(version="0.5.0", notes="fixed the thing")
        info = updater.check_for_update(
            "0.4.0", fetch=fetch_returning(json.dumps(manifest).encode())
        )
        assert info is not None
        assert info.version == "0.5.0"
        assert info.notes == "fixed the thing"
        assert info.download_url == manifest["artifact"]["url"]
        assert info.sha256 == manifest["artifact"]["sha256"]
        assert info.size_bytes == manifest["artifact"]["size_bytes"]

    def test_numeric_not_lexicographic_comparison(self) -> None:
        """0.10.0 is newer than 0.9.0 -- a string comparison would get
        this backwards."""
        manifest = make_manifest(version="0.10.0")
        info = updater.check_for_update(
            "0.9.0", fetch=fetch_returning(json.dumps(manifest).encode())
        )
        assert info is not None
        assert info.version == "0.10.0"

    def test_same_version_is_silent(self) -> None:
        manifest = make_manifest(version="0.4.0")
        info = updater.check_for_update(
            "0.4.0", fetch=fetch_returning(json.dumps(manifest).encode())
        )
        assert info is None

    def test_older_version_is_silent(self) -> None:
        manifest = make_manifest(version="0.3.0")
        info = updater.check_for_update(
            "0.4.0", fetch=fetch_returning(json.dumps(manifest).encode())
        )
        assert info is None

    def test_malformed_manifest_is_silent(self) -> None:
        info = updater.check_for_update("0.4.0", fetch=fetch_returning(b"not json at all"))
        assert info is None

    def test_manifest_missing_required_keys_is_silent(self) -> None:
        info = updater.check_for_update(
            "0.4.0", fetch=fetch_returning(json.dumps({"version": "9.0.0"}).encode())
        )
        assert info is None

    def test_unsupported_schema_version_is_silent(self) -> None:
        manifest = make_manifest(version="9.0.0")
        manifest["schema_version"] = 999
        info = updater.check_for_update(
            "0.4.0", fetch=fetch_returning(json.dumps(manifest).encode())
        )
        assert info is None

    def test_network_failure_is_silent(self) -> None:
        info = updater.check_for_update("0.4.0", fetch=fetch_raising(OSError("no route to host")))
        assert info is None

    def test_timeout_is_silent(self) -> None:
        import socket
        info = updater.check_for_update("0.4.0", fetch=fetch_raising(socket.timeout("timed out")))
        assert info is None

    def test_never_blocks_beyond_a_fast_synchronous_call(self) -> None:
        """Not a real network test -- just confirms nothing here sleeps or
        retries on its own; `fetch` is called exactly once."""
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            return json.dumps(make_manifest(version="0.1.0")).encode()

        updater.check_for_update("0.4.0", fetch=fetch)
        assert len(calls) == 1

    def test_pkgtools_unavailable_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom():
            raise ImportError("scripts/ not bundled")

        monkeypatch.setattr(updater, "_load_pkgtools_manifest", boom)
        info = updater.check_for_update(
            "0.4.0", fetch=fetch_returning(json.dumps(make_manifest(version="9.9.9")).encode())
        )
        assert info is None


class TestUpdateState:
    def test_starts_empty(self) -> None:
        assert updater.UpdateState().get() is None

    def test_set_then_get_round_trips(self) -> None:
        state = updater.UpdateState()
        info = updater.UpdateInfo(
            version="1.0.0", notes=None, download_url="u", sha256="a" * 64, size_bytes=1, manifest={}
        )
        state.set(info)
        assert state.get() is info


class TestCheckForUpdateInBackground:
    def test_populates_state_without_blocking_the_caller(self) -> None:
        release_fetch = threading.Event()

        def slow_fetch(url, timeout):
            release_fetch.wait(timeout=2)
            return json.dumps(make_manifest(version="9.9.9")).encode()

        state = updater.UpdateState()
        thread = updater.check_for_update_in_background("0.1.0", state, fetch=slow_fetch)

        # The call above must have returned immediately -- the fetch is
        # still blocked on release_fetch, so if we got here at all without
        # hanging, the background thread genuinely did not block us.
        assert state.get() is None  # not populated yet

        release_fetch.set()
        thread.join(timeout=2)
        assert state.get() is not None
        assert state.get().version == "9.9.9"

    def test_an_exception_in_the_background_thread_never_propagates(self) -> None:
        def exploding_fetch(url, timeout):
            raise RuntimeError("boom")

        state = updater.UpdateState()
        thread = updater.check_for_update_in_background("0.1.0", state, fetch=exploding_fetch)
        thread.join(timeout=2)
        # RuntimeError isn't one of check_for_update's own caught types,
        # but the background wrapper's own try/except must still catch it.
        assert state.get() is None


class TestDownloadAndVerifyUpdate:
    def _info_for(self, content: bytes, tmp_path: Path) -> updater.UpdateInfo:
        sha = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(sha256=sha, size_bytes=len(content))
        return updater.UpdateInfo(
            version=manifest["version"],
            notes=None,
            download_url=manifest["artifact"]["url"],
            sha256=sha,
            size_bytes=len(content),
            manifest=manifest,
        )

    def test_matching_sha256_succeeds_and_returns_the_path(self, tmp_path: Path) -> None:
        content = b"a fake release zip's bytes"
        info = self._info_for(content, tmp_path)

        def download_file(url, dest, timeout):
            dest.write_bytes(content)

        result = updater.download_and_verify_update(
            info, dest_dir=tmp_path / "downloads", download_file=download_file
        )

        assert result.is_file()
        assert result.read_bytes() == content

    def test_sha256_mismatch_rejects_the_artifact(self, tmp_path: Path) -> None:
        real_content = b"the real release bytes"
        info = self._info_for(real_content, tmp_path)

        def download_file(url, dest, timeout):
            dest.write_bytes(b"corrupted or tampered bytes")  # wrong content -> wrong hash

        with pytest.raises(updater.UpdateApplyError):
            updater.download_and_verify_update(
                info, dest_dir=tmp_path / "downloads", download_file=download_file
            )

        # Rejected artifact must not be left on disk.
        assert list((tmp_path / "downloads").glob("*")) == []

    def test_size_mismatch_rejects_the_artifact(self, tmp_path: Path) -> None:
        info = self._info_for(b"expected content, 17 bytes", tmp_path)

        def download_file(url, dest, timeout):
            dest.write_bytes(b"short")  # wrong size

        with pytest.raises(updater.UpdateApplyError):
            updater.download_and_verify_update(
                info, dest_dir=tmp_path / "downloads", download_file=download_file
            )
        assert list((tmp_path / "downloads").glob("*")) == []

    def test_download_failure_raises_and_leaves_nothing_behind(self, tmp_path: Path) -> None:
        info = self._info_for(b"content", tmp_path)

        def failing_download(url, dest, timeout):
            raise OSError("connection reset")

        with pytest.raises(updater.UpdateApplyError):
            updater.download_and_verify_update(
                info, dest_dir=tmp_path / "downloads", download_file=failing_download
            )
        assert list((tmp_path / "downloads").glob("*")) == []

    def test_pkgtools_unavailable_raises_update_apply_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        info = self._info_for(b"content", tmp_path)

        def boom():
            raise ImportError("scripts/ not bundled")

        monkeypatch.setattr(updater, "_load_pkgtools_manifest", boom)

        with pytest.raises(updater.UpdateApplyError):
            updater.download_and_verify_update(
                info, dest_dir=tmp_path / "downloads", download_file=lambda *a: None
            )


class TestApplyUpdate:
    def _make_bundle_zip(self, path: Path, *, wrapped_in_app_folder: bool) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            prefix = f"{updater.APP_NAME}/" if wrapped_in_app_folder else ""
            zf.writestr(f"{prefix}{updater.EXE_NAME}", "fake exe bytes")
            zf.writestr(f"{prefix}bin/ffmpeg.exe", "fake ffmpeg")

    def test_extracts_and_spawns_the_helper_with_correct_arguments(self, tmp_path: Path) -> None:
        artifact = tmp_path / "AshCaptions-0.5.0-win64.zip"
        self._make_bundle_zip(artifact, wrapped_in_app_folder=True)
        install_dir = tmp_path / "install"

        spawned: list[list[str]] = []
        updater.apply_update(
            artifact,
            has_running_job=lambda: False,
            install_dir=install_dir,
            extract_to=tmp_path / "staged",
            spawn_helper=spawned.append,
        )

        assert len(spawned) == 1
        argv = spawned[0]
        assert argv[0] == "powershell.exe"
        assert str(install_dir) in argv
        assert updater.EXE_NAME in argv
        # The unwrapped source dir (inside the AppName folder) was passed,
        # not the raw staging root.
        source_dir_arg = argv[argv.index("-SourceDir") + 1]
        assert Path(source_dir_arg).name == updater.APP_NAME
        assert (Path(source_dir_arg) / updater.EXE_NAME).is_file()

    def test_handles_a_zip_not_wrapped_in_an_app_folder(self, tmp_path: Path) -> None:
        artifact = tmp_path / "bundle.zip"
        self._make_bundle_zip(artifact, wrapped_in_app_folder=False)

        spawned: list[list[str]] = []
        updater.apply_update(
            artifact,
            has_running_job=lambda: False,
            install_dir=tmp_path / "install",
            extract_to=tmp_path / "staged",
            spawn_helper=spawned.append,
        )

        assert len(spawned) == 1
        source_dir_arg = spawned[0][spawned[0].index("-SourceDir") + 1]
        assert (Path(source_dir_arg) / updater.EXE_NAME).is_file()

    def test_a_zip_without_the_exe_raises_and_never_spawns_anything(self, tmp_path: Path) -> None:
        artifact = tmp_path / "bogus.zip"
        with zipfile.ZipFile(artifact, "w") as zf:
            zf.writestr("readme.txt", "not a real build")

        spawned: list[list[str]] = []
        with pytest.raises(updater.UpdateApplyError):
            updater.apply_update(
                artifact,
                has_running_job=lambda: False,
                install_dir=tmp_path / "install",
                extract_to=tmp_path / "staged",
                spawn_helper=spawned.append,
            )
        assert spawned == []

    def test_a_corrupt_zip_raises_and_never_spawns_anything(self, tmp_path: Path) -> None:
        artifact = tmp_path / "not_a_zip.zip"
        artifact.write_bytes(b"this is not a zip file at all")

        spawned: list[list[str]] = []
        with pytest.raises(updater.UpdateApplyError):
            updater.apply_update(
                artifact,
                has_running_job=lambda: False,
                install_dir=tmp_path / "install",
                extract_to=tmp_path / "staged",
                spawn_helper=spawned.append,
            )
        assert spawned == []


class TestApplyUpdateRefusesWhileAJobIsRunning:
    """The non-negotiable part: applying an update must never kill an
    in-progress caption job. This check lives in apply_update() itself,
    not just the UI, so no caller can bypass it.
    """

    def _make_bundle_zip(self, path: Path) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(updater.EXE_NAME, "fake exe bytes")

    def test_refuses_with_an_editor_facing_message_and_never_spawns(self, tmp_path: Path) -> None:
        artifact = tmp_path / "bundle.zip"
        self._make_bundle_zip(artifact)

        spawned: list[list[str]] = []
        with pytest.raises(updater.UpdateApplyError) as exc_info:
            updater.apply_update(
                artifact,
                has_running_job=lambda: True,
                install_dir=tmp_path / "install",
                extract_to=tmp_path / "staged",
                spawn_helper=spawned.append,
            )

        assert str(exc_info.value) == updater.JOB_RUNNING_MESSAGE
        assert spawned == []

    def test_refuses_before_ever_extracting_the_artifact(self, tmp_path: Path) -> None:
        """No side effects at all when refused -- not even the staging
        directory should appear."""
        artifact = tmp_path / "bundle.zip"
        self._make_bundle_zip(artifact)
        staging = tmp_path / "staged"

        with pytest.raises(updater.UpdateApplyError):
            updater.apply_update(
                artifact,
                has_running_job=lambda: True,
                install_dir=tmp_path / "install",
                extract_to=staging,
                spawn_helper=lambda argv: None,
            )

        assert not staging.exists()

    def test_a_job_that_starts_during_extraction_is_still_caught_by_the_recheck(
        self, tmp_path: Path
    ) -> None:
        """Simulates the TOCTOU window: has_running_job() says False on
        the first call (checked up front) but True by the time
        apply_update reaches its second check, immediately before the
        point of no return (the helper spawn) -- proving that second
        check is real, not decorative.
        """
        artifact = tmp_path / "bundle.zip"
        self._make_bundle_zip(artifact)

        calls = {"count": 0}

        def has_running_job() -> bool:
            calls["count"] += 1
            return calls["count"] > 1  # clear on the first check, running by the second

        spawned: list[list[str]] = []
        with pytest.raises(updater.UpdateApplyError) as exc_info:
            updater.apply_update(
                artifact,
                has_running_job=has_running_job,
                install_dir=tmp_path / "install",
                extract_to=tmp_path / "staged",
                spawn_helper=spawned.append,
            )

        assert str(exc_info.value) == updater.JOB_RUNNING_MESSAGE
        assert calls["count"] == 2  # both the upfront and the pre-spawn check ran
        assert spawned == []  # never reached the point of no return

    def test_proceeds_normally_when_no_job_is_running(self, tmp_path: Path) -> None:
        artifact = tmp_path / "bundle.zip"
        self._make_bundle_zip(artifact)

        spawned: list[list[str]] = []
        updater.apply_update(
            artifact,
            has_running_job=lambda: False,
            install_dir=tmp_path / "install",
            extract_to=tmp_path / "staged",
            spawn_helper=spawned.append,
        )

        assert len(spawned) == 1
