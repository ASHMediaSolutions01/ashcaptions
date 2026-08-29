"""Application assembly and process entry point (spec section 8).

Wires the five architecture components together: the SQLite-backed job
store, the ``QueueAdapter``/``LanguageCatalogue`` bridges into the web
layer, the watch-folder watcher, the single-worker queue, the FastAPI
control page, retention cleanup, and the pystray tray icon that is this
app's actual identity on an editor's desktop.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import msvcrt
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from typing import IO

from ash_captions import styles
from ash_captions.config import MAX_PORT_PROBES, Settings
from ash_captions.pipeline import JobWorker, Watcher
from ash_captions.pipeline.db import JobStore
from ash_captions.web import create_app, run_server
from ash_captions.web.models import JobOptions

from .adapter import QueueAdapter
from .catalogue import LanguageCatalogue
from .lifecycle import RetentionSweeper, configure_logging
from .runner import build_run_job
from .updater import UpdateState, check_for_update_in_background

logger = logging.getLogger("ash_captions.app")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI surface (spec section 6, 11.4): the Task Scheduler "run at
    logon" entry launches with *no* arguments, and that bare invocation
    must be tray-only -- six editors do not want a browser tab appearing
    on every boot. The desktop shortcut and Start Menu entry instead pass
    ``--open``, which additionally opens the control page. Both launch
    modes otherwise start every component identically.
    """
    parser = argparse.ArgumentParser(prog="ash-captions")
    parser.add_argument(
        "--open",
        action="store_true",
        help="Also open the control page in the default browser on startup "
        "(used by the desktop shortcut / Start Menu entry, not the logon task).",
    )
    return parser.parse_args(argv)


def _find_open_port(preferred: int, *, max_probes: int) -> int:
    """Probe upward from ``preferred`` for a free port -- a stuck previous
    instance (or anything else) holding the default port must not stop the
    control page from starting.

    Deliberately does *not* set ``SO_REUSEADDR``: on Windows that option
    lets ``bind()`` succeed even against a port another socket is actively
    listening on, which would defeat the whole point of this probe.
    """
    for candidate in range(preferred, preferred + max_probes):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"No free port found in range {preferred}-{preferred + max_probes - 1}.")


def _acquire_single_instance_lock(lock_path: Path) -> IO[str] | None:
    """Try to become the only running instance. Returns an open file
    object holding an OS-level exclusive byte-range lock on success, or
    ``None`` if another instance already holds it.

    Two real processes launching a `pystray.Icon` never collide with each
    other (each gets its own address space, so the Win32 window class
    name it registers -- derived from `id(self)` -- never collides across
    processes; see `tray.build_tray_icon`'s docstring for the *same*-
    process collision that guards against). But two live ASH Captions
    processes absolutely would collide on everything downstream of the
    tray icon: both would watch the same folder and could both pick up
    the same dropped file (`pipeline.JobStore.fetch_oldest_pending()` +
    `mark_running()` aren't one atomic transaction, so two processes could
    both grab the same pending job), both would try to bind a control-page
    port, and an editor would see two tray icons. This lock -- held for
    the life of the caller's process -- is what stops the second launch
    before any of that.

    The lock is released by Windows the moment this process exits, cleanly
    or via a crash, so a stale lock can never survive a dead process --
    same crash-recovery philosophy as `JobStore.reset_stale_running()`:
    nothing here needs manual cleanup on restart.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def _warn_already_running() -> None:
    """A second launch (an editor double-clicking the desktop shortcut
    while the logon task's instance is already running, say) must never
    surface a raw error -- there is no console to show one in anyway
    (spec section 11: windowed build). A native message box is the only
    UI available before -- or instead of -- a tray icon.
    """
    try:
        MB_OK = 0x0
        MB_ICONINFORMATION = 0x40
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None,
            "ASH Captions is already running. Look for its icon in the system tray.",
            "ASH Captions",
            MB_OK | MB_ICONINFORMATION,
        )
    except Exception:  # noqa: BLE001 - the message box is a courtesy, never load-bearing
        logger.warning("ASH Captions is already running.")


def _enqueue_watch_file(adapter: QueueAdapter, settings: Settings, path: Path) -> None:
    """``Watcher.on_ready`` callback: submit a dropped file with the
    configured defaults (spec section 6: "the 80% path -- no UI at all").
    """
    options = JobOptions(
        language=settings.default_language,
        dialect=settings.default_dialect,
        preset=settings.default_preset,
        burn_in=settings.default_burn,
        translate_to_english=settings.default_translate,
    )
    try:
        adapter.submit(path, options)
    except Exception:  # noqa: BLE001 - a bad drop must never kill the watcher thread
        logger.exception("Failed to enqueue watch-folder file %s", path)


def _validate_default_preset(settings: Settings) -> None:
    """Diagnostic only -- never blocks startup.

    ``styles.resolve_style()`` already falls back to the default style for
    an unknown name (spec 7A.4), so a bad ``default_preset`` can't fail a
    job. But that fallback firing for the *configured default* -- the
    style every watch-folder job uses (spec section 6's 80% path) -- would
    otherwise go unnoticed until a client sees plain CLEAN captions
    instead of whatever look was actually configured. A settings typo or a
    renamed style file should show up in the log the moment the app
    starts, not get discovered downstream.
    """
    if settings.default_preset not in styles.list_styles():
        logger.warning(
            "Settings.default_preset %r does not match any shipped or user "
            "style; every watch-folder job will silently fall back to %r "
            "until this is fixed.",
            settings.default_preset,
            styles.DEFAULT_STYLE.name,
        )


def _current_version() -> str:
    """The running app's own version, for the update checker to compare
    the manifest against. Falls back to "0.0.0" (never a crash) if
    package metadata isn't discoverable -- which just makes every real
    release look newer, the safe-by-default direction for a check that
    must never block or misbehave at startup.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ash-captions")
    except PackageNotFoundError:
        logger.warning("Could not determine the running version; update checks may misfire.")
        return "0.0.0"


def build_application(settings: Settings):
    """Construct every component, wired together, without starting any of
    them. Split out from ``main()`` so tests (and any future embedding)
    can assemble the app without a real browser, tray icon, or uvicorn
    server.
    """
    settings.ensure_dirs()
    _validate_default_preset(settings)

    store = JobStore(settings.db_path)
    adapter = QueueAdapter(store, out_dir=settings.out_dir)
    catalogue = LanguageCatalogue()
    run_job = build_run_job(settings, watch_dir=settings.in_dir)
    worker = JobWorker(store=adapter.notifying_store, run_job=run_job)
    watcher = Watcher(
        settings.in_dir,
        on_ready=lambda path: _enqueue_watch_file(adapter, settings, path),
    )
    sweeper = RetentionSweeper(settings.out_dir, retention_days=settings.retention_days)
    app = create_app(adapter, catalogue)

    return app, adapter, worker, watcher, sweeper


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    settings = Settings.load()
    configure_logging(settings.log_path)

    # Held for the rest of this function's lifetime (see docstring) -- do
    # not let `lock` go out of scope or get closed before shutdown.
    lock_path = settings.db_path.parent / "ash-captions.lock"
    lock = _acquire_single_instance_lock(lock_path)
    if lock is None:
        logger.warning("Another ASH Captions instance is already running; exiting.")
        _warn_already_running()
        return

    logger.info("ASH Captions starting (open_browser=%s)", args.open)

    app, _adapter, worker, watcher, sweeper = build_application(settings)

    # Exposed on app.state (same pattern as app.state.queue/catalogue) so a
    # future control-page route can read the last check's result; the tray
    # menu reads it directly. The check itself never blocks startup and
    # never blocks on a dead network -- see updater.check_for_update.
    update_state = UpdateState()
    app.state.update_state = update_state
    check_for_update_in_background(_current_version(), update_state)

    port = _find_open_port(settings.port, max_probes=MAX_PORT_PROBES)
    url = f"http://127.0.0.1:{port}"

    worker.start()
    watcher.start()
    sweeper.start()

    server_thread = threading.Thread(
        target=lambda: run_server(app, port=port), name="ash-captions-web", daemon=True
    )
    server_thread.start()

    # Bare invocation (the logon task) is tray-only -- no browser tab on
    # every boot. --open (the desktop shortcut / Start Menu entry) also
    # opens the control page.
    if args.open:
        webbrowser.open(url)

    def shutdown() -> None:
        logger.info("ASH Captions shutting down")
        watcher.stop()
        worker.stop()
        sweeper.stop()
        lock.close()  # release the single-instance lock explicitly, don't wait on process exit

    try:
        from .tray import build_tray_icon

        icon = build_tray_icon(url=url, settings=settings, on_quit=shutdown)
    except RuntimeError:
        # No usable tray backend on this machine. There is still no
        # console to fall back to (this ships as a windowed PyInstaller
        # build), so keep the process -- and its worker/watcher threads --
        # alive rather than exiting immediately.
        logger.warning("Tray icon unavailable; running headless until interrupted.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        shutdown()
        return

    # Blocks on the main thread until the tray's Quit item fires, which
    # calls `shutdown()` itself (see build_tray_icon's on_quit) before
    # icon.stop() lets run() return -- shutdown must not run a second time
    # here, though pipeline.JobWorker/Watcher and RetentionSweeper's
    # stop() are idempotent regardless.
    icon.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
