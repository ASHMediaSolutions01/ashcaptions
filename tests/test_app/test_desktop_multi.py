"""Browse... for several videos at once: the Windows dialog with
Multiselect on, one path per printed line."""
from __future__ import annotations

import subprocess

import pytest

from ash_captions.app import desktop
from ash_captions.web.interfaces import FilePicker, PickerBusyError


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class TestDialogCommandMultiple:
    def test_single_stays_single(self):
        script = desktop.dialog_command()[-1]
        assert "$dialog.Multiselect = $false" in script
        assert "'Choose a video to caption'" in script

    def test_multiple_turns_multiselect_on_and_prints_one_path_per_line(self):
        script = desktop.dialog_command(multiple=True)[-1]
        assert "$dialog.Multiselect = $true" in script
        assert "$dialog.FileNames -join" in script
        assert "'Choose videos to caption'" in script


class TestPickVideos:
    def test_returns_every_printed_path(self):
        seen: list[list[str]] = []

        def run(command, timeout):
            seen.append(command)
            return _Completed("D:\\a.mp4\r\nD:\\b c.mov\n\n")

        picker = desktop.WindowsFilePicker(run=run)
        assert isinstance(picker, FilePicker)
        assert picker.pick_videos() == ["D:\\a.mp4", "D:\\b c.mov"]
        assert "$dialog.Multiselect = $true" in seen[0][-1]

    def test_cancel_is_an_empty_list(self):
        picker = desktop.WindowsFilePicker(run=lambda command, timeout: _Completed(""))
        assert picker.pick_videos() == []

    def test_timeout_and_missing_powershell_are_empty_not_a_crash(self):
        def timed_out(command, timeout):
            raise subprocess.TimeoutExpired(command, timeout)

        def missing(command, timeout):
            raise FileNotFoundError("powershell")

        assert desktop.WindowsFilePicker(run=timed_out).pick_videos() == []
        assert desktop.WindowsFilePicker(run=missing).pick_videos() == []

    def test_shares_the_one_dialog_lock_with_pick_video(self):
        picker = desktop.WindowsFilePicker(run=lambda command, timeout: _Completed("D:\\a.mp4"))
        picker._lock.acquire()
        try:
            with pytest.raises(PickerBusyError):
                picker.pick_videos()
        finally:
            picker._lock.release()
        assert picker.pick_videos() == ["D:\\a.mp4"]
