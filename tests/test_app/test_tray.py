"""Tests for tray.py.

pystray/Pillow are declared, installed dependencies -- these tests run
for real against the actual library, not a guess at its API. Only
`test_build_tray_icon_raises_runtime_error_without_pystray` simulates
absence (by monkeypatching `pystray` to `None`), to exercise the guarded
import's degrade path on a machine that somehow lacks it; the rest must
never be skipped just because pystray happens to be missing from whatever
environment runs the suite.

Exactly one test here (`test_quit_click_on_a_real_running_icon_makes_run_return`)
constructs a real `pystray.Icon`. Every other test drives `_build_menu()`
directly -- `pystray.Menu`/`MenuItem` are plain data with no OS side
effects, unlike `Icon.__init__`, which registers a native Win32 window
class named from `id(self)` on construction. That id is normally unique
per instance, but CPython can reuse a freed object's id for a new one,
and Windows never unregisters a class on its own (registration is
process-lifetime, not tied to the Python object's lifetime) -- so many
short-lived real `Icon`s built back-to-back in one test process can
collide on `RegisterClassEx` in a way the shipped app, which builds
exactly one `Icon` per process, never does. See `tray.py`'s docstrings.
"""

from __future__ import annotations

import threading

import pytest

from ash_captions.app import tray
from ash_captions.app.updater import UpdateInfo, UpdateState
from ash_captions.config import Settings


def make_settings(tmp_path, opened: list | None = None):
    settings = Settings(out_dir=tmp_path / "out", log_path=tmp_path / "log.txt")
    opener = (opened.append if opened is not None else (lambda _p: None))
    return settings, opener


def test_module_imports_without_pystray_installed() -> None:
    # If this file's import of `tray` above didn't raise, the guard works.
    assert hasattr(tray, "build_tray_icon")


def test_build_tray_icon_raises_runtime_error_without_pystray(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(tray, "pystray", None)
    settings, _ = make_settings(tmp_path)

    with pytest.raises(RuntimeError):
        tray.build_tray_icon(url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None)


def test_menu_has_the_four_expected_items(tmp_path) -> None:
    settings, opener = make_settings(tmp_path)
    menu = tray._build_menu(url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, open_path=opener)

    labels = [item.text for item in menu]
    assert labels == ["Open control page", "Open output folder", "Open log file", "Quit"]


def make_update_info(version: str = "0.2.0") -> UpdateInfo:
    return UpdateInfo(
        version=version, notes=None, download_url="https://example.com/x.zip",
        sha256="a" * 64, size_bytes=1, manifest={},
    )


class TestUpdateMenuItem:
    """The tray's half of the decided UX (spec section 11.4): a signpost
    only, never an apply action. It appears only once update_state holds
    an update and opens the control page -- the same as "Open control
    page" -- rather than applying anything itself.
    """

    def test_hidden_when_no_update_state_is_given(self, tmp_path) -> None:
        settings, opener = make_settings(tmp_path)
        menu = tray._build_menu(
            url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, open_path=opener
        )
        assert "Update available" not in [item.text for item in menu]

    def test_hidden_when_update_state_holds_no_update(self, tmp_path) -> None:
        settings, opener = make_settings(tmp_path)
        state = UpdateState()  # empty -- no update found (yet)
        menu = tray._build_menu(
            url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None,
            open_path=opener, update_state=state,
        )
        assert len(list(menu)) == 4  # the update item is filtered out, not just blank

    def test_visible_with_the_version_once_an_update_is_found(self, tmp_path) -> None:
        settings, opener = make_settings(tmp_path)
        state = UpdateState()
        state.set(make_update_info("0.5.0"))
        menu = tray._build_menu(
            url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None,
            open_path=opener, update_state=state,
        )

        labels = [item.text for item in menu]
        assert "Update available (v0.5.0)" in labels
        assert len(labels) == 5

    def test_appears_the_moment_state_is_populated_without_rebuilding_the_menu(self, tmp_path) -> None:
        """The same live `menu` object must reflect a later state change
        -- pystray re-evaluates text/visible on each render, so nothing
        here should need to construct a new Menu."""
        settings, opener = make_settings(tmp_path)
        state = UpdateState()
        menu = tray._build_menu(
            url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None,
            open_path=opener, update_state=state,
        )
        assert "Update available" not in [item.text for item in menu]

        state.set(make_update_info("0.3.0"))

        assert "Update available (v0.3.0)" in [item.text for item in menu]

    def test_clicking_it_opens_the_control_page_not_an_apply_action(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        settings, opener = make_settings(tmp_path)
        state = UpdateState()
        state.set(make_update_info("0.5.0"))
        url = "http://127.0.0.1:8756"
        menu = tray._build_menu(
            url=url, settings=settings, on_quit=lambda: None, open_path=opener, update_state=state
        )
        opened_urls: list = []
        monkeypatch.setattr(tray.webbrowser, "open", opened_urls.append)

        item = next(i for i in menu if i.text.startswith("Update available"))
        item(None)

        assert opened_urls == [url]


def test_quit_item_calls_icon_stop_and_on_quit(tmp_path) -> None:
    settings, opener = make_settings(tmp_path)
    called = []
    menu = tray._build_menu(
        url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: called.append(True), open_path=opener
    )

    class FakeIcon:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    fake_icon = FakeIcon()
    quit_item = next(item for item in menu if item.text == "Quit")
    quit_item(fake_icon)  # MenuItem.__call__(icon) invokes action(icon, item)

    assert fake_icon.stopped is True
    assert called == [True]


def test_open_output_folder_calls_opener_with_out_dir_and_creates_it(tmp_path) -> None:
    opened: list = []
    settings, opener = make_settings(tmp_path, opened)
    menu = tray._build_menu(url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, open_path=opener)

    item = next(i for i in menu if i.text == "Open output folder")
    item(None)

    assert opened == [str(settings.out_dir)]
    assert settings.out_dir.is_dir()  # created if it didn't exist yet


def test_open_log_file_calls_opener_only_when_the_log_exists(tmp_path) -> None:
    opened: list = []
    settings, opener = make_settings(tmp_path, opened)
    menu = tray._build_menu(url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, open_path=opener)
    item = next(i for i in menu if i.text == "Open log file")

    item(None)
    assert opened == []  # log doesn't exist yet -- must not try to open a missing file

    settings.log_path.write_text("hello", encoding="utf-8")
    item(None)
    assert opened == [str(settings.log_path)]


def test_open_control_page_opens_the_url_in_the_browser(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings, opener = make_settings(tmp_path)
    menu = tray._build_menu(url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: None, open_path=opener)
    opened_urls: list = []
    monkeypatch.setattr(tray.webbrowser, "open", opened_urls.append)

    item = next(i for i in menu if i.text == "Open control page")
    item(None)

    assert opened_urls == ["http://127.0.0.1:8756"]


def test_quit_click_on_a_real_running_icon_makes_run_return(tmp_path) -> None:
    """The only test in this file that builds a real `pystray.Icon` (see
    module docstring for why that has to be deliberate and singular). It
    proves the actual promise a tray Quit item makes to an editor: a real,
    running `icon.run()` (the same blocking call `__main__.main()` makes on
    the main thread) genuinely returns once Quit is clicked, and
    `on_quit()` fires before it does. If this ever hangs, an editor's only
    way out of the app is Task Manager.
    """
    settings, opener = make_settings(tmp_path)
    quit_called = []
    icon = tray.build_tray_icon(
        url="http://127.0.0.1:8756", settings=settings, on_quit=lambda: quit_called.append(True), opener=opener
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
