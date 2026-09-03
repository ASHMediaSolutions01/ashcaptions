"""Tests for the apply-side hardening in updater.py: a stale staging tree
is cleared before extraction, and the helper spawns with breakaway from
the app's job object (retrying without it only if the OS refuses).
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from ash_captions.app import updater
from ash_captions.app.jobobject import CREATE_BREAKAWAY_FROM_JOB


def _artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "AshCaptions-1.2.3.zip"
    with zipfile.ZipFile(artifact, "w") as zf:
        zf.writestr(f"{updater.APP_NAME}/{updater.EXE_NAME}", b"new exe")
    return artifact


def test_stale_staging_contents_are_removed_before_extracting(tmp_path: Path) -> None:
    staging = tmp_path / "staged"
    stale = staging / updater.APP_NAME / "old-dll-from-a-failed-apply.dll"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    spawned: list[list[str]] = []

    updater.apply_update(
        _artifact(tmp_path),
        has_running_job=lambda: False,
        install_dir=tmp_path / "install",
        extract_to=staging,
        spawn_helper=spawned.append,
    )

    assert not stale.exists()
    assert (staging / updater.APP_NAME / updater.EXE_NAME).is_file()
    assert spawned and "-ParentProcessId" in spawned[0]


def test_refuses_to_apply_over_a_source_checkout(tmp_path: Path) -> None:
    """The tray path reaches apply_update directly, around the web routes'
    own refusal: robocopy /MIR over a git checkout would wipe it."""
    install_dir = tmp_path / "checkout"
    (install_dir / ".git").mkdir(parents=True)
    spawned: list[list[str]] = []

    with pytest.raises(updater.UpdateApplyError, match="source checkout"):
        updater.apply_update(
            _artifact(tmp_path),
            has_running_job=lambda: False,
            install_dir=install_dir,
            extract_to=tmp_path / "staged",
            spawn_helper=spawned.append,
        )

    assert spawned == []
    assert not (tmp_path / "staged").exists()


def test_helper_script_waits_six_hours_not_twenty_minutes() -> None:
    assert f"AddSeconds({6 * 3600})" in updater._APPLY_HELPER_TEMPLATE
    assert "AddSeconds(1200)" not in updater._APPLY_HELPER_TEMPLATE


def test_default_spawn_helper_breaks_away_from_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_popen(argv, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    updater._default_spawn_helper(["powershell.exe"])

    assert len(calls) == 1
    assert calls[0]["creationflags"] & CREATE_BREAKAWAY_FROM_JOB
    assert calls[0]["creationflags"] & subprocess.DETACHED_PROCESS


def test_default_spawn_helper_retries_without_breakaway_if_refused(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[dict] = []

    def fake_popen(argv, **kwargs):
        calls.append(kwargs)
        if kwargs["creationflags"] & CREATE_BREAKAWAY_FROM_JOB:
            raise OSError(5, "Access is denied")
        return object()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    with caplog.at_level("WARNING", logger="ash_captions.app.updater"):
        updater._default_spawn_helper(["powershell.exe"])

    assert len(calls) == 2
    assert not calls[1]["creationflags"] & CREATE_BREAKAWAY_FROM_JOB
    assert any("could not break away" in r.getMessage() for r in caplog.records)
