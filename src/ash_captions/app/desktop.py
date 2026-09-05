"""The editor's own desktop: a native Open File dialog and an Explorer
window (spec section 4.4 -- one editor, one PC, the app runs on it).

Both are the real implementations of the small protocols in
``web/interfaces.py`` (``FilePicker``, ``PathRevealer``). ``create_app()``
builds them by default; tests inject fakes.

The dialog is a PowerShell one-liner over ``System.Windows.Forms``, run
as its own STA process: Python's main thread belongs to pystray, and a
Win32 common dialog needs a single-threaded apartment of its own. The
dialog is owned by an invisible top-most form so it opens above the
browser rather than behind it. Only one dialog runs at a time; a second
request while it is open gets ``PickerBusyError`` (a 409 on the wire).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from ash_captions.web.interfaces import PickerBusyError
from ash_captions.web.models import ALLOWED_VIDEO_EXTENSIONS

logger = logging.getLogger("ash_captions.app.desktop")

DIALOG_TIMEOUT_SECONDS = 600  # ten minutes: a real person browsing a slow share
DIALOG_TITLE = "Choose a video to caption"

_VIDEO_GLOB = ";".join(f"*{ext}" for ext in ALLOWED_VIDEO_EXTENSIONS)
_DIALOG_FILTER = f"Video files ({_VIDEO_GLOB})|{_VIDEO_GLOB}|All files (*.*)|*.*"

# PowerShell writes the chosen path (UTF-8, no newline) and nothing else;
# a cancelled dialog writes nothing. The owner form is never shown: it
# exists only so the dialog inherits TopMost and lands in front.
_DIALOG_SCRIPT = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '{title}'
$dialog.Filter = '{filter}'
$dialog.Multiselect = $false
$dialog.CheckFileExists = $true
$dialog.RestoreDirectory = $true
$owner.Add_Shown({{ $owner.Activate() }})
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.Write($dialog.FileName) }}
"""


def dialog_command(*, title: str = DIALOG_TITLE) -> list[str]:
    """The exact process line the picker runs (exposed for tests)."""
    script = _DIALOG_SCRIPT.format(title=title.replace("'", "''"), filter=_DIALOG_FILTER)
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


class WindowsFilePicker:
    """Implements ``web.interfaces.FilePicker`` with the real Open File
    dialog. ``run`` is the subprocess runner (injectable for tests); it
    must return an object with ``stdout`` (str) like ``subprocess.run``."""

    def __init__(self, *, timeout: float = DIALOG_TIMEOUT_SECONDS, run=None) -> None:
        self._timeout = timeout
        self._run = run or _run_hidden
        self._lock = threading.Lock()

    def pick_video(self) -> str | None:
        if not self._lock.acquire(blocking=False):
            raise PickerBusyError("A file dialog is already open.")
        try:
            try:
                completed = self._run(dialog_command(), self._timeout)
            except subprocess.TimeoutExpired:
                logger.info("File dialog timed out after %s s; treating as cancelled.", self._timeout)
                return None
            except OSError as exc:
                logger.warning("Could not open the file dialog: %s", exc)
                return None
            chosen = (completed.stdout or "").strip()
            return chosen or None
        finally:
            self._lock.release()


def _run_hidden(command: list[str], timeout: float):
    """``subprocess.run`` without a console window flashing up."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
        check=False,
    )


class ExplorerRevealer:
    """Implements ``web.interfaces.PathRevealer`` with Windows Explorer.
    ``launch`` is the process launcher (injectable for tests)."""

    def __init__(self, *, launch=None, open_folder=None) -> None:
        self._launch = launch or _launch_detached
        self._open_folder = open_folder or _startfile

    def reveal(self, path: Path) -> None:
        target = Path(path)
        if target.is_file():
            # /select, opens the parent folder with the file highlighted.
            # The comma is part of Explorer's own syntax; one argv element.
            self._launch(["explorer.exe", f"/select,{target}"])
        elif target.is_dir():
            self._open_folder(str(target))
        else:
            raise FileNotFoundError(str(target))


def _launch_detached(command: list[str]) -> None:
    subprocess.Popen(command, close_fds=True)


def _startfile(path: str) -> None:
    os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - Windows-only, this app's only target
