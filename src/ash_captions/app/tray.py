"""System tray icon: the app's actual identity on an editor's desktop
(spec sections 6, 8, 11).

There is deliberately no console window -- an editor must never be able
to accidentally close a terminal and kill the tool. The tray icon is what
stays visible and controllable instead: open the control page, open the
output folder, open the log file, or quit.

``pystray`` (and Pillow, for the icon image) pull in a platform-specific
GUI backend at import time. That import is guarded so this module can
still be imported -- and its pure logic tested -- in a headless/test
environment that lacks a working tray backend; only actually building or
running an icon requires the real thing.
"""

from __future__ import annotations

import logging
import os
import webbrowser
from typing import Callable

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - exercised wherever pystray/PIL are absent
    pystray = None
    Image = None
    ImageDraw = None

from ash_captions.config import Settings

logger = logging.getLogger("ash_captions.app.tray")

ICON_SIZE = 64
ICON_FILL = (30, 144, 255, 255)  # dodger blue; a placeholder until real branding exists


def _build_icon_image():
    """A simple, dependency-free icon: a solid circle."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = ICON_SIZE // 16
    draw.ellipse((margin, margin, ICON_SIZE - margin, ICON_SIZE - margin), fill=ICON_FILL)
    return image


def _build_menu(
    *,
    url: str,
    settings: Settings,
    on_quit: Callable[[], None],
    open_path: Callable[[str], None],
):
    """Build the menu: open the control page, open the output folder, open
    the log file, quit.

    Split out from ``build_tray_icon`` so tests can exercise these
    callbacks without constructing a real ``pystray.Icon`` -- unlike
    ``pystray.Menu``/``MenuItem`` (plain data), ``Icon.__init__`` registers
    a native Win32 window class per instance, and building many real icons
    in one test process risks colliding class names (see
    ``build_tray_icon``'s docstring).
    """

    def open_control_page(icon, item) -> None:
        webbrowser.open(url)

    def open_output_folder(icon, item) -> None:
        settings.out_dir.mkdir(parents=True, exist_ok=True)
        open_path(str(settings.out_dir))

    def open_log_file(icon, item) -> None:
        if settings.log_path.is_file():
            open_path(str(settings.log_path))
        else:
            logger.warning("Log file %s does not exist yet.", settings.log_path)

    def quit_app(icon, item) -> None:
        icon.stop()
        on_quit()

    return pystray.Menu(
        pystray.MenuItem("Open control page", open_control_page, default=True),
        pystray.MenuItem("Open output folder", open_output_folder),
        pystray.MenuItem("Open log file", open_log_file),
        pystray.MenuItem("Quit", quit_app),
    )


def build_tray_icon(
    *,
    url: str,
    settings: Settings,
    on_quit: Callable[[], None],
    opener: Callable[[str], None] | None = None,
):
    """Build (but do not run) the tray icon.

    ``icon.run()`` blocks and must be called from the main thread -- pystray's
    Windows backend is not reliably usable off it.

    Raises ``RuntimeError`` if pystray/PIL aren't available; callers decide
    how to degrade (see ``__main__.main``).

    Each call constructs a real ``pystray.Icon``, which on Windows calls
    ``RegisterClassEx`` for a window class named from ``id(self)`` --
    normally unique per instance, but CPython can reuse a freed object's
    id for a new one, and Windows never unregisters a class on its own
    (it's process-lifetime, not tied to the Python object). One process
    (the real app) creating exactly one icon never hits this; many
    short-lived `Icon`s in one test process can. See ``_build_menu`` and
    ``tests/test_app/test_tray.py`` for how the test suite avoids it.
    """
    if pystray is None:
        raise RuntimeError("pystray is not available; cannot build a tray icon.")

    # os.startfile only exists on Windows, which is this app's only target
    # platform (spec section 6) -- resolved lazily so importing this module
    # elsewhere never fails on the attribute lookup.
    open_path: Callable[[str], None] = opener or os.startfile  # type: ignore[attr-defined]
    menu = _build_menu(url=url, settings=settings, on_quit=on_quit, open_path=open_path)

    return pystray.Icon("ash_captions", _build_icon_image(), "ASH Captions", menu)
