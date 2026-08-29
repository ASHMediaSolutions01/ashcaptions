"""Static checks on installer/Install-AshCaptions.bat.

Not executed -- it ends in `pause`, which is meant for an editor sitting at
the keyboard, not a test runner. Its only job is to forward arguments to
install.ps1 with a bypassed execution policy scoped to that one process, so
this checks the text does that correctly.
"""

from __future__ import annotations

from pathlib import Path

BAT_PATH = Path(__file__).resolve().parents[2] / "installer" / "Install-AshCaptions.bat"


def test_bat_exists():
    assert BAT_PATH.is_file()


def test_bat_invokes_install_ps1_next_to_itself():
    text = BAT_PATH.read_text(encoding="utf-8")
    assert "install.ps1" in text
    assert "%SCRIPT_DIR%" in text or "%~dp0" in text


def test_bat_bypasses_execution_policy_for_this_process_only():
    text = BAT_PATH.read_text(encoding="utf-8")
    assert "-ExecutionPolicy Bypass" in text
    # Must not touch the machine-wide policy.
    assert "Set-ExecutionPolicy" not in text


def test_bat_forwards_arguments():
    text = BAT_PATH.read_text(encoding="utf-8")
    assert "%*" in text


def test_bat_surfaces_failure_to_the_user():
    text = BAT_PATH.read_text(encoding="utf-8")
    assert "ERRORLEVEL" in text
    assert "pause" in text.lower()
