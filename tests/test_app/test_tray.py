"""Tests for tray.py.

pystray/Pillow are declared, installed dependencies -- these tests run
for real against the actual library, not a guess at its API. Only
`test_build_tray_icon_raises_runtime_error_without_pystray` simulates
absence (by monkeypatching `pystray` to `None`), to exercise the guarded
import's degrade path on a machine that somehow lacks it; the rest must
never be skipped just because pystray happens to be missing from whatever
environment runs the suite.
"""

from __future__ import annotations

import threading

import pytest

from ash_captions.app import tray
from ash_captions.config import Settings


def test_module_imports_without_pystray_installed() -> None:
    # If this file's import of `tray` above didn't raise, the guard works.
    assert hasattr(tray, "build_tray_icon")


def test_build_tray_icon_raises_runtime_error_without_pystray(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(tray, "pystray", None)
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")

    with pytest.raises(RuntimeError):
        tray.build_tray_icon(url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None)


def test_build_tray_icon_menu_has_the_four_expected_items(tmp_path) -> None:
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, opener=lambda _p: None
    )
    labels = [item.text for item in icon.menu.items]
    assert labels == ["Open control page", "Open output folder", "Open log file", "Quit"]


def test_quit_menu_item_stops_icon_and_calls_on_quit(tmp_path) -> None:
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    called = []
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756",
        settings=settings,
        on_quit=lambda: called.append(True),
        opener=lambda _p: None,
    )

    class FakeIcon:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    fake_icon = FakeIcon()
    quit_item = next(item for item in icon.menu.items if item.text == "Quit")
    quit_item(fake_icon)  # MenuItem.__call__(icon) invokes action(icon, item)

    assert fake_icon.stopped is True
    assert called == [True]


def test_open_output_folder_calls_opener_with_out_dir_and_creates_it(tmp_path) -> None:
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    opened = []
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, opener=opened.append
    )

    item = next(i for i in icon.menu.items if i.text == "Open output folder")
    item(icon)

    assert opened == [str(settings.out_dir)]
    assert settings.out_dir.is_dir()  # created if it didn't exist yet


def test_open_log_file_calls_opener_only_when_the_log_exists(tmp_path) -> None:
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    opened = []
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, opener=opened.append
    )
    item = next(i for i in icon.menu.items if i.text == "Open log file")

    item(icon)
    assert opened == []  # log doesn't exist yet -- must not try to open a missing file

    settings.log_path.write_text("hello", encoding="utf-8")
    item(icon)
    assert opened == [str(settings.log_path)]


def test_open_control_page_opens_the_url_in_the_browser(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, opener=lambda _p: None
    )
    opened_urls = []
    monkeypatch.setattr(tray.webbrowser, "open", opened_urls.append)

    item = next(i for i in icon.menu.items if i.text == "Open control page")
    item(icon)

    assert opened_urls == ["http://127.0.0.1:8756"]


def test_quit_click_on_a_real_running_icon_makes_run_return(tmp_path) -> None:
    """The mocked-FakeIcon test above proves the callback wiring; this
    proves the actual promise a tray Quit item makes to an editor: a real,
    running `icon.run()` (the same blocking call `__main__.main()` makes on
    the main thread) genuinely returns once Quit is clicked, and
    `on_quit()` fires before it does. If this ever hangs, an editor's only
    way out of the app is Task Manager.
    """
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    quit_called = []
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756",
        settings=settings,
        on_quit=lambda: quit_called.append(True),
        opener=lambda _p: None,
    )

    run_returned = threading.Event()

    def run_icon() -> None:
        icon.run()
        run_returned.set()

    thread = threading.Thread(target=run_icon, daemon=True)
    thread.start()
    # Give pystray's real Windows backend a moment to actually initialize
    # the tray icon and its message loop before we click Quit.
    threading.Event().wait(1.5)

    quit_item = next(item for item in icon.menu.items if item.text == "Quit")
    quit_item(icon)  # simulates a real click: same call pystray itself makes

    thread.join(timeout=5)

    assert run_returned.is_set(), "icon.run() did not return after Quit was clicked"
    assert quit_called == [True]
