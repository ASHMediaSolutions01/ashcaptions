"""v0.5 installer additions: preflight, plain-words download errors, and the
uninstaller (design 2026-09-04, section 4).

Two kinds of test, both against the real files in installer/:

* static -- read the .ps1/.bat text and assert the checks and wording exist,
  verbatim. Runs on any OS and needs no PowerShell.
* real -- run powershell.exe against a throwaway temp tree with every
  location override set (-InstallDir/-DataRoot/-TaskName/-DesktopDir/
  -StartMenuProgramsDir/-StartupDir), exactly like test_install_ps1.py, so
  nothing here ever touches the real install, C:\\AshCaptions, the real
  Desktop/Start Menu/Startup folders or the AshCaptionsTray task. Scheduled
  Tasks are registered under unique names and unregistered in finally blocks.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parents[2] / "installer"
INSTALL_PS1 = INSTALLER_DIR / "install.ps1"
UNINSTALL_PS1 = INSTALLER_DIR / "uninstall.ps1"
UNINSTALL_BAT = INSTALLER_DIR / "Uninstall-AshCaptions.bat"

WINDOWS_ONLY = pytest.mark.skipif(
    platform.system() != "Windows" or shutil.which("powershell") is None,
    reason="the installer scripts are Windows PowerShell scripts",
)


def _run(script: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ps(command: str) -> str:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30
    ).stdout.strip()


def _unregister(task_name: str) -> None:
    _ps(f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue")


@pytest.fixture
def fake_bundle(tmp_path) -> Path:
    bundle_dir = tmp_path / "source" / "AshCaptions"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "AshCaptions.exe").write_bytes(b"fake exe for tests")
    return bundle_dir.parent  # -Source points at the folder *containing* AshCaptions/


@pytest.fixture
def scratch(tmp_path) -> dict[str, str]:
    """Every location override, pointing into tmp_path, plus a unique task name."""
    return {
        "-InstallDir": str(tmp_path / "install"),
        "-DataRoot": str(tmp_path / "data"),
        "-TaskName": f"AshCaptionsTest_{uuid.uuid4().hex[:8]}",
        "-DesktopDir": str(tmp_path / "desktop"),
        "-StartMenuProgramsDir": str(tmp_path / "startmenu"),
        "-StartupDir": str(tmp_path / "startup"),
    }


def _args(scratch: dict[str, str]) -> list[str]:
    return [item for pair in scratch.items() for item in pair]


# ---------------------------------------------------------------------------
# preflight: static
# ---------------------------------------------------------------------------


def test_install_ps1_has_a_preflight_function_run_before_any_download():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "function Test-Preflight" in text
    assert "function Write-PreflightReport" in text
    main = text.split("# --- main ---", 1)[1]
    assert main.index("Test-Preflight -InstallDir") < main.index("Resolve-BundleSource -Source")


def test_install_ps1_checks_64_bit_windows_or_stops():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "[Environment]::Is64BitOperatingSystem" in text
    assert "ASH Captions needs 64-bit Windows" in text


def test_install_ps1_warns_below_windows_build_17763():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "$MinWindowsBuild = 17763" in text
    assert "CurrentBuildNumber" in text


def test_install_ps1_stops_without_4gb_free_and_prints_the_number():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "[double]$MinFreeGB = 4" in text
    assert "AvailableFreeSpace" in text
    assert "Not enough free space on" in text
    assert "GB free, $MinFreeGB GB needed" in text


def test_install_ps1_enables_tls12():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "[Net.SecurityProtocolType]::Tls12" in text


def test_install_ps1_warns_when_long_paths_are_off():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "LongPathsEnabled" in text
    assert "needs an administrator" in text


# ---------------------------------------------------------------------------
# preflight: real
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
def test_check_only_reports_the_preflight_inside_the_json(scratch, tmp_path):
    result = _run(INSTALL_PS1, "-CheckOnly", *_args(scratch))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)

    preflight = payload["preflight"]
    assert isinstance(preflight["ok"], bool)
    checks = {c["name"]: c for c in preflight["checks"]}
    assert set(checks) == {"windows_64bit", "windows_build", "free_space", "tls12", "long_paths"}
    for check in checks.values():
        assert check["status"] in ("ok", "warn", "fail"), check
        assert check["detail"]
    assert "GB" in checks["free_space"]["detail"]
    # This build box is 64-bit; a fail here means the check itself is wrong.
    assert checks["windows_64bit"]["status"] == "ok"
    # -CheckOnly still creates nothing.
    assert not (tmp_path / "install").exists()
    assert not (tmp_path / "data").exists()


@WINDOWS_ONLY
def test_install_stops_when_the_drive_is_too_full(fake_bundle, scratch, tmp_path):
    try:
        result = _run(INSTALL_PS1, "-Source", str(fake_bundle), "-MinFreeGB", "999999", *_args(scratch))
        assert result.returncode == 2, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert "Not enough free space on" in result.stdout
        assert "GB free, 999999 GB needed" in result.stdout
        assert "ASH Captions was not installed." in result.stdout
        assert not (tmp_path / "install").exists()
        assert not (tmp_path / "data").exists()
        assert not (tmp_path / "desktop").exists()
        assert _ps(f"(Get-ScheduledTask -TaskName '{scratch['-TaskName']}' -ErrorAction SilentlyContinue).TaskName") == ""
    finally:
        _unregister(scratch["-TaskName"])


@WINDOWS_ONLY
def test_install_prints_the_preflight_then_installs_when_it_passes(fake_bundle, scratch, tmp_path):
    try:
        result = _run(INSTALL_PS1, "-Source", str(fake_bundle), *_args(scratch))
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert "==> Checking this PC" in result.stdout
        assert "64-bit Windows" in result.stdout
        assert "GB free on" in result.stdout
        assert (tmp_path / "install" / "AshCaptions.exe").is_file()
    finally:
        _unregister(scratch["-TaskName"])
