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


# ---------------------------------------------------------------------------
# download errors in plain words
# ---------------------------------------------------------------------------


def test_install_ps1_explains_download_failures_instead_of_a_dotnet_exception():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "function Get-Download" in text
    assert "Check the internet connection; if this PC uses a proxy, ask Ghazi" in text
    # Both downloads (manifest, then the zip) must go through the wrapper.
    assert text.count("Get-Download -Url") == 2
    assert "Invoke-WebRequest" not in text.split("function Get-Download", 1)[1].split("function Resolve-BundleSource", 1)[1]


@WINDOWS_ONLY
def test_a_refused_download_is_reported_in_plain_words(scratch, tmp_path):
    # Port 9 on loopback is closed: the connection is refused at once, no
    # network needed and no real URL touched. The preflight passes first
    # (this box is 64-bit with space), so the failure is the download itself.
    result = _run(INSTALL_PS1, "-ManifestUrl", "http://127.0.0.1:9/manifest.json", *_args(scratch))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Could not download the release list from http://127.0.0.1:9/manifest.json" in output
    assert "Check the internet connection; if this PC uses a proxy, ask Ghazi" in output
    assert not (tmp_path / "install").exists()


# ---------------------------------------------------------------------------
# uninstaller: static
# ---------------------------------------------------------------------------


def test_uninstall_ps1_exists_and_takes_the_same_location_overrides_as_install():
    text = UNINSTALL_PS1.read_text(encoding="utf-8")
    for name in ("$InstallDir", "$DataRoot", "$TaskName", "$DesktopDir", "$StartMenuProgramsDir", "$StartupDir"):
        assert name in text.split("$ErrorActionPreference", 1)[0], f"{name} missing from the param block"
    assert "[switch]$RemoveData" in text
    assert "[switch]$CheckOnly" in text
    assert "$DataRoot = 'C:\\AshCaptions'" in text
    assert "$TaskName = 'AshCaptionsTray'" in text


def test_uninstall_ps1_does_each_step_the_spec_lists():
    text = UNINSTALL_PS1.read_text(encoding="utf-8")
    assert "Stop-Process" in text  # quit the running app
    assert "Unregister-ScheduledTask" in text  # the logon task
    assert "'ASH Captions.lnk'" in text  # Desktop, Start Menu and Startup shortcuts
    assert "Remove-Item -Recurse -Force" in text  # the install folder
    assert "Kept (not deleted):" in text  # says what it kept
    assert "-RemoveData" in text


def test_uninstall_ps1_only_stops_the_exe_from_the_install_folder():
    # Killing every AshCaptions.exe on the machine would also kill a real
    # install while a test uninstalls a scratch one.
    text = UNINSTALL_PS1.read_text(encoding="utf-8")
    assert "$_.Path.StartsWith($InstallDir" in text


def test_uninstall_bat_mirrors_the_install_bat():
    text = UNINSTALL_BAT.read_text(encoding="utf-8")
    assert "uninstall.ps1" in text
    assert "%~dp0" in text
    assert "-ExecutionPolicy Bypass" in text
    assert "Set-ExecutionPolicy" not in text
    assert "%*" in text
    assert "ERRORLEVEL" in text
    assert "pause" in text.lower()


# ---------------------------------------------------------------------------
# uninstaller: real round trips against a scratch install
# ---------------------------------------------------------------------------


def _install_scratch(fake_bundle: Path, scratch: dict[str, str]) -> None:
    result = _run(INSTALL_PS1, "-Source", str(fake_bundle), *_args(scratch))
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@WINDOWS_ONLY
def test_uninstall_removes_the_install_and_keeps_the_data(fake_bundle, scratch, tmp_path):
    install_dir, data_root = tmp_path / "install", tmp_path / "data"
    desktop_lnk = tmp_path / "desktop" / "ASH Captions.lnk"
    start_menu_lnk = tmp_path / "startmenu" / "ASH Captions.lnk"
    startup_lnk = tmp_path / "startup" / "ASH Captions.lnk"
    task_name = scratch["-TaskName"]
    try:
        _install_scratch(fake_bundle, scratch)
        # Simulate the Startup-folder fallback as well, so both removal paths run.
        startup_lnk.parent.mkdir(parents=True, exist_ok=True)
        startup_lnk.write_bytes(b"fake shortcut")
        # Something the app wrote into the data root once it ran.
        (data_root / "settings.json").write_text("{}", encoding="utf-8")
        (data_root / "out" / "reel").mkdir(parents=True)
        (data_root / "out" / "reel" / "reel.srt").write_text("1\n", encoding="utf-8")
        assert _ps(f"(Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue).TaskName") == task_name

        result = _run(UNINSTALL_PS1, *_args(scratch))
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        assert not install_dir.exists()
        assert not desktop_lnk.exists()
        assert not start_menu_lnk.exists()
        assert not startup_lnk.exists()
        assert _ps(f"(Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue).TaskName") == ""
        # Data kept, and the script said so, naming the folder.
        assert (data_root / "out" / "reel" / "reel.srt").is_file()
        assert (data_root / "settings.json").is_file()
        assert "Kept (not deleted):" in result.stdout
        assert str(data_root) in result.stdout
        assert "-RemoveData" in result.stdout
        assert "ASH Captions has been uninstalled." in result.stdout
    finally:
        _unregister(task_name)


@WINDOWS_ONLY
def test_uninstall_remove_data_deletes_the_data_root_too(fake_bundle, scratch, tmp_path):
    data_root = tmp_path / "data"
    try:
        _install_scratch(fake_bundle, scratch)
        (data_root / "settings.json").write_text("{}", encoding="utf-8")

        result = _run(UNINSTALL_PS1, "-RemoveData", *_args(scratch))
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        assert not (tmp_path / "install").exists()
        assert not data_root.exists()
        assert "Kept (not deleted):" not in result.stdout
        assert f"Removed {data_root}" in result.stdout
    finally:
        _unregister(scratch["-TaskName"])


@WINDOWS_ONLY
def test_uninstall_check_only_reports_without_changing_anything(fake_bundle, scratch, tmp_path):
    try:
        _install_scratch(fake_bundle, scratch)

        result = _run(UNINSTALL_PS1, "-CheckOnly", *_args(scratch))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = json.loads(result.stdout)

        assert payload["install_dir"] == str(tmp_path / "install")
        assert payload["install_dir_exists"] is True
        assert payload["data_root"] == str(tmp_path / "data")
        assert payload["data_root_exists"] is True
        assert payload["remove_data"] is False
        assert payload["task_name"] == scratch["-TaskName"]
        assert payload["task_exists"] is True
        assert payload["startup_shortcut_exists"] is False
        assert sorted(payload["shortcuts"]) == sorted(
            [str(tmp_path / "desktop" / "ASH Captions.lnk"), str(tmp_path / "startmenu" / "ASH Captions.lnk")]
        )
        assert payload["app_running"] is False

        assert (tmp_path / "install" / "AshCaptions.exe").is_file()  # nothing removed
        assert _ps(f"(Get-ScheduledTask -TaskName '{scratch['-TaskName']}' -ErrorAction SilentlyContinue).TaskName") == scratch["-TaskName"]
    finally:
        _unregister(scratch["-TaskName"])


@WINDOWS_ONLY
def test_uninstall_with_nothing_installed_is_a_clean_no_op(scratch, tmp_path):
    # Task already gone, no folders, no shortcuts: exactly the state the
    # rehearsal install at Temp\ashinst4 is in, and the state a second
    # double-click of the uninstaller finds.
    result = _run(UNINSTALL_PS1, *_args(scratch))
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "ASH Captions is not running." in result.stdout
    assert "No start-at-logon entry found." in result.stdout
    assert "already removed" in result.stdout
    assert "ASH Captions has been uninstalled." in result.stdout
