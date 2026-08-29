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
