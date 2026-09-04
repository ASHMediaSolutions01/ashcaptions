"""The update helper must outlive the app that spawned it, mirror the new
files and relaunch. The first real update (0.4.0 -> 0.4.1, 2026-09-04)
downloaded, extracted and handed off, and nothing came back: powershell.exe
started with DETACHED_PROCESS exits immediately. This runs the real helper
template through the real spawn function, from inside a real kill-on-close
job object, with the parent gone before the helper does its work."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ash_captions.app import updater

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only update helper")

_PARENT = """
import os, sys, time
from ash_captions.app import jobobject, updater
jobobject.assign_current_process()
script, src, dst = sys.argv[1:4]
updater._default_spawn_helper([
    "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script,
    "-ParentProcessId", str(os.getpid()), "-SourceDir", src, "-InstallDir", dst, "-ExeName", "Relaunch.cmd",
])
time.sleep(1)
os._exit(0)
"""

_RELAUNCH_CMD = '@echo off\r\necho started > "%~dp0relaunched.txt"\r\n'


def test_helper_outlives_the_app_mirrors_the_install_and_relaunches(tmp_path: Path) -> None:
    src, dst = tmp_path / "staged", tmp_path / "install"
    src.mkdir()
    dst.mkdir()
    (src / "payload.txt").write_text("new build", encoding="utf-8")
    (src / "Relaunch.cmd").write_text(_RELAUNCH_CMD, encoding="utf-8", newline="")
    (dst / "stale.txt").write_text("old build", encoding="utf-8")
    script = tmp_path / "apply_update.ps1"
    script.write_text(updater._APPLY_HELPER_TEMPLATE, encoding="utf-8")

    src_dir = str(Path(__file__).resolve().parents[2] / "src")
    parent = subprocess.run(
        [sys.executable, "-c", _PARENT, str(script), str(src), str(dst)],
        env={**os.environ, "PYTHONPATH": src_dir},
        capture_output=True, text=True, timeout=60,
    )
    assert parent.returncode == 0, parent.stderr

    deadline = time.time() + 40
    while time.time() < deadline and not (dst / "relaunched.txt").is_file():
        time.sleep(0.25)

    assert (dst / "payload.txt").read_text(encoding="utf-8") == "new build"
    assert not (dst / "stale.txt").exists(), "robocopy /MIR should have removed the old file"
    assert (dst / "relaunched.txt").is_file(), "the helper never relaunched the app"
