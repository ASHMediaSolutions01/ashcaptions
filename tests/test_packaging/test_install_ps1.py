"""Drives the real installer/install.ps1.

The -CheckOnly tests need nothing but the script itself. The full end-to-end
test actually installs into a throwaway temp tree (never the real
%LOCALAPPDATA%\\AshCaptions, C:\\AshCaptions, Desktop or Start Menu -- see
install.ps1's -InstallDir/-DataRoot/-DesktopDir/-StartMenuProgramsDir
test-only overrides) and registers a real, uniquely-named Scheduled Task
that the test unregisters again in a finally block, to prove the installer's
idempotency requirement (run twice -> no duplicate task) against the real
Windows Task Scheduler rather than a mock of it.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "installer" / "install.ps1"

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows" or shutil.which("powershell") is None,
    reason="install.ps1 is a Windows PowerShell script",
)


def _run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_script_exists():
    assert SCRIPT_PATH.is_file()


def test_check_only_reports_plan_without_side_effects(tmp_path):
    install_dir = tmp_path / "install"
    data_root = tmp_path / "data"

    result = _run(
        "-CheckOnly",
        "-InstallDir", str(install_dir),
        "-DataRoot", str(data_root),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)

    assert payload["install_dir"] == str(install_dir)
    assert payload["data_root"] == str(data_root)
    assert payload["in_dir"] == str(data_root / "in")
    assert payload["out_dir"] == str(data_root / "out")
    assert payload["glossary_dir"] == str(data_root / "glossaries")
    assert isinstance(payload["gpu"]["Present"], bool)
    assert payload["task_exists"] is False

    # -CheckOnly must not create anything.
    assert not install_dir.exists()
    assert not data_root.exists()


def test_check_only_default_manifest_url_is_set():
    """A zero-argument install must resolve against the real public artifacts
    repo, not a placeholder org -- see scripts/release.py's DEFAULT_RELEASES_REPO."""
    result = _run("-CheckOnly")
    payload = json.loads(result.stdout)
    assert payload["manifest_url"] == (
        "https://github.com/ASHMediaSolutions01/ashcaptions-releases/"
        "releases/latest/download/manifest.json"
    )


@pytest.fixture
def fake_bundle(tmp_path) -> Path:
    bundle_dir = tmp_path / "source" / "AshCaptions"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "AshCaptions.exe").write_bytes(b"fake exe for tests")
    return bundle_dir.parent  # -Source points at the folder *containing* AshCaptions/


def test_full_install_is_idempotent(fake_bundle, tmp_path):
    install_dir = tmp_path / "install"
    data_root = tmp_path / "data"
    desktop_dir = tmp_path / "desktop"
    start_menu_dir = tmp_path / "startmenu"
    task_name = f"AshCaptionsTest_{uuid.uuid4().hex[:8]}"

    common_args = [
        "-Source", str(fake_bundle),
        "-InstallDir", str(install_dir),
        "-DataRoot", str(data_root),
        "-TaskName", task_name,
        "-DesktopDir", str(desktop_dir),
        "-StartMenuProgramsDir", str(start_menu_dir),
    ]

    try:
        first = _run(*common_args)
        assert first.returncode == 0, f"stderr: {first.stderr}"

        assert (install_dir / "AshCaptions.exe").is_file()
        assert (data_root / "in").is_dir()
        assert (data_root / "out").is_dir()
        assert (data_root / "glossaries").is_dir()
        assert (desktop_dir / "ASH Captions.lnk").is_file()
        assert (start_menu_dir / "ASH Captions.lnk").is_file()

        # Shortcuts open the control page; the logon task deliberately does
        # not (see Register-LogonTask's docstring -- six editors do not want
        # a browser tab appearing on every single login).
        for lnk in (desktop_dir / "ASH Captions.lnk", start_menu_dir / "ASH Captions.lnk"):
            args_check = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 f"(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}').Arguments"],
                capture_output=True, text=True, timeout=30,
            )
            assert args_check.stdout.strip() == "--open"

        task_check = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"(Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue).TaskName"],
            capture_output=True, text=True, timeout=30,
        )
        assert task_check.stdout.strip() == task_name

        task_action_check = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"(Get-ScheduledTask -TaskName '{task_name}').Actions[0].Arguments"],
            capture_output=True, text=True, timeout=30,
        )
        assert task_action_check.stdout.strip() == ""  # no --open at logon

        # --- second run: must not duplicate the task or break the install ---
        second = _run(*common_args)
        assert second.returncode == 0, f"stderr: {second.stderr}"
        assert "already exists" in second.stdout

        count_check = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"@(Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=30,
        )
        assert count_check.stdout.strip() == "1"

        assert (install_dir / "AshCaptions.exe").is_file()
    finally:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=30,
        )


def test_missing_source_raises(tmp_path):
    result = _run(
        "-Source", str(tmp_path / "does-not-exist"),
        "-InstallDir", str(tmp_path / "install"),
        "-DataRoot", str(tmp_path / "data"),
        "-TaskName", f"AshCaptionsTest_{uuid.uuid4().hex[:8]}",
        "-DesktopDir", str(tmp_path / "desktop"),
        "-StartMenuProgramsDir", str(tmp_path / "startmenu"),
    )
    assert result.returncode != 0


def test_bundle_missing_exe_raises(tmp_path):
    bad_source = tmp_path / "source"
    bad_source.mkdir()
    (bad_source / "not-the-exe.txt").write_text("oops", encoding="utf-8")

    result = _run(
        "-Source", str(bad_source),
        "-InstallDir", str(tmp_path / "install"),
        "-DataRoot", str(tmp_path / "data"),
        "-TaskName", f"AshCaptionsTest_{uuid.uuid4().hex[:8]}",
        "-DesktopDir", str(tmp_path / "desktop"),
        "-StartMenuProgramsDir", str(tmp_path / "startmenu"),
    )
    assert result.returncode != 0
    assert "does not contain" in (result.stdout + result.stderr)
