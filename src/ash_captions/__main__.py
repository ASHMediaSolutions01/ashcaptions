"""Entry point for `python -m ash_captions` and the packaged console
script (``pyproject.toml``'s ``ash-captions = "ash_captions.__main__:main"``,
which is what the PyInstaller build's ``AshCaptions.exe`` -- and in turn
the installer's desktop shortcut, Start Menu entry, and Task Scheduler
"run at logon" entry -- all launch).

Deliberately thin: the actual assembly (job store, queue adapter,
language catalogue, watcher, worker, control page, tray icon) lives in
``ash_captions.app.__main__``. This module exists only so that entry
point has somewhere to point -- see that module's docstring, and its
``--open`` flag, for what running this actually does.
"""

from __future__ import annotations

from ash_captions.app.__main__ import main

if __name__ == "__main__":
    main()
