"""System tray icon: the app's actual identity on an editor's desktop
(spec sections 6, 8, 11).

There is deliberately no console window -- an editor must never be able
to accidentally close a terminal and kill the tool. The tray icon is what
stays visible and controllable instead: open the control page, open the
output folder, open the log file, or quit -- plus a signpost to an
available update, when one exists (the control page carries the primary
banner and the actual apply action; the tray only points there).

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

from .updater import UpdateState

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
    update_state: UpdateState | None = None,
):
    """Build the menu: update available (only when one is), open the
    control page, open the output folder, open the log file, quit.

    Split out from ``build_tray_icon`` so tests can exercise these
    callbacks without constructing a real ``pystray.Icon`` -- unlike
    ``pystray.Menu``/``MenuItem`` (plain data), ``Icon.__init__`` registers
    a native Win32 window class per instance, and building many real icons
    in one test process risks colliding class names (see
    ``build_tray_icon``'s docstring).
    """

    def open_control_page(icon, item) -> None:
        webbrowser.open(url)

    def update_item_text(item) -> str:
        info = update_state.get() if update_state is not None else None
        return f"Update available (v{info.version})" if info is not None else "Update available"

    def update_item_visible(item) -> bool:
        return update_state is not None and update_state.get() is not None

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
        # The tray is a signpost, not an action: this opens the control
        # page (where the primary update banner lives) rather than
        # applying anything itself. Re-evaluated by pystray on every
        # render (text/visible are both callables, per pystray's own
        # MenuItem), so it appears the moment update_state is populated
        # without needing the menu to be rebuilt.
        pystray.MenuItem(update_item_text, open_control_page, visible=update_item_visible),
        pystray.MenuItem("Open control page", open_control_page, default=True),
        pystray.MenuItem("Open output folder", open_output_folder),
        pystray.MenuItem("Open log file", open_log_file),
        pystray.MenuItem("Quit", quit_app),
    )


def subscribe_job_notifications(icon, adapter) -> Callable[[object], None]:
    """Show a tray balloon when a job finishes or fails.

    ``adapter`` is the ``QueueAdapter``; it calls every entry in its
    ``on_job_finished`` list with the finished web ``Job``. ``icon`` only
    needs a pystray-shaped ``notify(message, title)``. Returns the
    subscriber so a caller (or test) can remove it again.

    Wiring, in ``__main__._run`` after ``build_tray_icon`` returns::

        subscribe_job_notifications(icon, _adapter)
    """

    def on_finished(job) -> None:
        if job.status == "done" or getattr(job.status, "value", None) == "done":
            title, message = "Captions ready", f"{job.filename} is done. Open the queue to pick a look."
        else:
            reason = (getattr(job, "error", None) or "something went wrong").strip()
            title, message = "Captioning failed", f"{job.filename}: {reason[:120]}"
        try:
            icon.notify(message, title)
        except Exception:  # noqa: BLE001 - a balloon is a courtesy, never load-bearing
            logger.warning("Could not show the tray notification for %s", job.filename, exc_info=True)

    adapter.on_job_finished.append(on_finished)
    return on_finished


def build_tray_icon(
    *,
    url: str,
    settings: Settings,
    on_quit: Callable[[], None],
    opener: Callable[[str], None] | None = None,
    update_state: UpdateState | None = None,
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
    menu = _build_menu(
        url=url, settings=settings, on_quit=on_quit, open_path=open_path, update_state=update_state
    )

    return pystray.Icon("ash_captions", _build_icon_image(), "ASH Captions", menu)
