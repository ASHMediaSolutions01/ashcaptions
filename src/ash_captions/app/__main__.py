"""Application assembly and process entry point (spec section 8).

Wires the five architecture components together: the SQLite-backed job
store, the ``QueueAdapter``/``LanguageCatalogue`` bridges into the web
layer, the watch-folder watcher, the single-worker queue, the FastAPI
control page, retention cleanup, and the pystray tray icon that is this
app's actual identity on an editor's desktop.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from ash_captions.config import MAX_PORT_PROBES, Settings
from ash_captions.pipeline import JobWorker, Watcher
from ash_captions.pipeline.db import JobStore
from ash_captions.web import create_app, run_server
from ash_captions.web.models import JobOptions

from .adapter import QueueAdapter
from .catalogue import LanguageCatalogue
from .lifecycle import RetentionSweeper, configure_logging
from .runner import build_run_job

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


def build_application(settings: Settings):
    """Construct every component, wired together, without starting any of
    them. Split out from ``main()`` so tests (and any future embedding)
    can assemble the app without a real browser, tray icon, or uvicorn
    server.
    """
    settings.ensure_dirs()

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
    logger.info("ASH Captions starting (open_browser=%s)", args.open)

    app, _adapter, worker, watcher, sweeper = build_application(settings)

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
