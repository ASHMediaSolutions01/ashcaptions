"""app/desktop.py: the Windows file picker (its PowerShell line, the
one-dialog-at-a-time lock, cancel and timeout) and the Explorer revealer
-- all through injected runners, so no dialog ever opens under pytest."""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from ash_captions.app import desktop
from ash_captions.web.interfaces import FilePicker, PathRevealer, PickerBusyError


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class TestDialogCommand:
    def test_runs_powershell_in_an_sta_without_a_profile(self):
        command = desktop.dialog_command()
        assert command[0] == "powershell"
        assert "-STA" in command and "-NoProfile" in command and "-NonInteractive" in command
        script = command[-1]
        assert "System.Windows.Forms.OpenFileDialog" in script
        assert "*.mp4" in script and "*.mov" in script
        assert "$owner.TopMost = $true" in script  # opens above the browser
        assert "[Console]::Out.Write($dialog.FileName)" in script

    def test_title_is_quoted_for_powershell(self):
        script = desktop.dialog_command(title="Editor's pick")[-1]
        assert "'Editor''s pick'" in script


class TestWindowsFilePicker:
    def test_returns_the_path_powershell_printed(self):
        seen: list[list[str]] = []
        picker = desktop.WindowsFilePicker(run=lambda command, timeout: (seen.append(command), _Completed("D:\\a.mp4\n"))[1])
        assert isinstance(picker, FilePicker)
        assert picker.pick_video() == "D:\\a.mp4"
        assert seen and seen[0][0] == "powershell"

    def test_cancel_is_none(self):
        picker = desktop.WindowsFilePicker(run=lambda command, timeout: _Completed(""))
        assert picker.pick_video() is None

    def test_timeout_is_none_and_uses_ten_minutes(self):
        timeouts: list[float] = []

        def run(command, timeout):
            timeouts.append(timeout)
            raise subprocess.TimeoutExpired(command, timeout)

        picker = desktop.WindowsFilePicker(run=run)
        assert picker.pick_video() is None
        assert timeouts == [600]

    def test_missing_powershell_is_none_not_a_crash(self):
        def run(command, timeout):
            raise FileNotFoundError("powershell")

        assert desktop.WindowsFilePicker(run=run).pick_video() is None

    def test_second_call_while_the_dialog_is_open_is_busy(self):
        opened = threading.Event()
        release = threading.Event()

        def run(command, timeout):
            opened.set()
            release.wait(5)
            return _Completed("D:\\b.mp4")

        picker = desktop.WindowsFilePicker(run=run)
        results: list = []
        first = threading.Thread(target=lambda: results.append(picker.pick_video()))
        first.start()
        assert opened.wait(5)
        with pytest.raises(PickerBusyError):
            picker.pick_video()
        release.set()
        first.join(5)
        assert results == ["D:\\b.mp4"]
        # and it is free again afterwards
        picker2 = desktop.WindowsFilePicker(run=lambda command, timeout: _Completed(""))
        assert picker2.pick_video() is None


class TestExplorerRevealer:
    def test_a_file_is_selected_inside_its_folder(self, tmp_path):
        target = tmp_path / "reel.captioned.mp4"
        target.write_bytes(b"x")
        launched: list[list[str]] = []
        opened: list[str] = []
        revealer = desktop.ExplorerRevealer(launch=launched.append, open_folder=opened.append)
        assert isinstance(revealer, PathRevealer)
        revealer.reveal(target)
        assert launched == [["explorer.exe", f"/select,{target}"]]
        assert opened == []

    def test_a_folder_is_opened(self, tmp_path):
        launched: list[list[str]] = []
        opened: list[str] = []
        desktop.ExplorerRevealer(launch=launched.append, open_folder=opened.append).reveal(tmp_path)
        assert opened == [str(tmp_path)]
        assert launched == []

    def test_a_missing_path_raises(self, tmp_path):
        revealer = desktop.ExplorerRevealer(launch=lambda c: None, open_folder=lambda p: None)
        with pytest.raises(FileNotFoundError):
            revealer.reveal(Path(tmp_path / "gone"))
