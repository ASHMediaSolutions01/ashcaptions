"""Application assembly and process entry point (spec section 8).

Wires the five architecture components together: the SQLite-backed job
store, the ``QueueAdapter``/``LanguageCatalogue`` bridges into the web
layer, the watch-folder watcher, the single-worker queue, the FastAPI
control page, retention cleanup, and the pystray tray icon that is this
app's actual identity on an editor's desktop.

``main()`` itself is one big try/except: this ships as a windowed build
with no console, so an unwritable data root, a dead network ``in_dir`` or
an exhausted port range used to make the exe vanish with nothing logged.
Now it logs the traceback and shows one message box naming the log file.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import msvcrt
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from typing import IO

from ash_captions import styles
from ash_captions.config import MAX_PORT_PROBES, Settings, data_root
from ash_captions.pipeline import JobWorker, Watcher
from ash_captions.pipeline.db import JobStatus, JobStore
from ash_captions.web import create_app, run_server
from ash_captions.web.models import JobOptions
from ash_captions.web.update_adapter import UpdaterAdapter

from . import jobobject
from .adapter import QueueAdapter
from .catalogue import LanguageCatalogue
from .lifecycle import RetentionSweeper, configure_logging, folder_is_live, sweep_tmp_dir
from .updater import clean_update_leftovers
from .runner import SharedTranscriber, _is_within, build_run_job
from .runner_util import accepted_kwargs

try:  # the web layer's "is the in-app updater usable here" answer, when it has one
    from ash_captions.web.runtime import updates_supported
except ImportError:  # pragma: no cover - older web package
    updates_supported = None  # type: ignore[assignment]
from .update_flow import (  # noqa: F401 - re-exported: tests and older callers import these from here
    UPDATE_SHUTDOWN_WATCHDOG_SECONDS,
    apply_update_shutdown as _apply_update_shutdown,
    apply_update_when_idle,
    shutdown_with_watchdog as _shutdown_with_watchdog,
)
from .updater import UpdateState, check_for_update_in_background

logger = logging.getLogger("ash_captions.app")

MB_OK = 0x0
MB_ICONINFORMATION = 0x40
MB_ICONERROR = 0x10


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

    Two live ASH Captions processes would collide on everything downstream
    of the tray icon: both would watch the same folder and could both pick
    up the same dropped file (`pipeline.JobStore.fetch_oldest_pending()` +
    `mark_running()` aren't one atomic transaction), both would try to
    bind a control-page port, and an editor would see two tray icons. This
    lock -- held for the life of the caller's process -- stops the second
    launch before any of that.

    The lock is released by Windows the moment this process exits, cleanly
    or via a crash, so a stale lock can never survive a dead process --
    same crash-recovery philosophy as `JobStore.reset_stale_running()`:
    nothing here needs manual cleanup on restart. A ``PermissionError``
    opening the file (a data root this account can't write) propagates:
    ``main()`` turns it into a message that names the folder.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def _message_box(text: str, *, icon: int = MB_ICONINFORMATION) -> None:
    """A native message box is the only UI available before -- or instead
    of -- a tray icon (spec section 11: windowed build, no console). Never
    load-bearing: if it can't be shown, the log line already exists."""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "ASH Captions", MB_OK | icon)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - courtesy only
        logger.warning("Could not show message box: %s", text)


def _warn_already_running() -> None:
    """A second launch (an editor double-clicking the desktop shortcut
    while the logon task's instance is already running, say) must never
    surface a raw error -- there is no console to show one in anyway.
    """
    try:
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
    style every watch-folder job uses -- would otherwise go unnoticed
    until a client sees plain CLEAN captions instead of whatever look was
    actually configured.
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


def _has_running_job(store: JobStore) -> bool:
    """The exact, correct answer to ``updater.apply_update``'s required
    ``has_running_job`` question, defined once here. Exposed as
    ``app.state.has_running_job`` (same pattern as ``app.state.update_state``).
    """
    return bool(store.list_jobs(status=JobStatus.RUNNING))


def _queued_watch_paths(store: JobStore, in_dir: Path) -> list[Path]:
    """Every non-done job's input inside ``in_dir`` -- what the watcher
    must remember across a restart so it doesn't re-report them (the
    database's one-live-job-per-input index is the second line)."""
    return [
        Path(job.input_path)
        for job in store.list_jobs()
        if job.status != JobStatus.DONE and _is_within(Path(job.input_path), in_dir)
    ]


def _shared_preview_renderer(settings: Settings, run_job):
    """The style editor's preview renderer sharing the queue's one loaded
    Whisper model (web owner's request) -- only when this tree's
    ``InProcessPreviewRenderer`` accepts ``transcriber``/``settings``;
    otherwise ``create_app`` builds its own default."""
    try:
        from ash_captions.web.preview_adapter import InProcessPreviewRenderer
    except ImportError:  # pragma: no cover
        return None
    getter = getattr(run_job, "get_transcriber", None)
    if getter is None:
        return None
    if {"transcriber", "settings"} <= accepted_kwargs(InProcessPreviewRenderer.__init__, ("transcriber", "settings")):
        return InProcessPreviewRenderer(transcriber=SharedTranscriber(getter), settings=settings)
    return None


def _start_update_check(update_state: UpdateState) -> None:
    """Background update check -- skipped where the web layer says the
    in-app updater isn't usable (a source checkout, an unfrozen run)."""
    if updates_supported is not None and not updates_supported():
        logger.info("Update checks disabled: not a frozen install (source checkout or dev run).")
        return
    check_for_update_in_background(_current_version(), update_state)


def build_application(settings: Settings, *, lock: IO[str] | None = None):
    """Construct every component, wired together, without starting any of
    them. Split out from ``main()`` so tests (and any future embedding)
    can assemble the app without a real browser, tray icon, or uvicorn
    server.

    ``lock`` is the single-instance lock ``main()`` already holds by the
    time it calls this -- passed through only so the update-apply shutdown
    path can release it as part of its own teardown.
    """
    settings.ensure_dirs()
    _validate_default_preset(settings)
    sweep_tmp_dir(settings.tmp_dir)
    clean_update_leftovers(data_root() / "updates")

    store = JobStore(settings.db_path)
    adapter = QueueAdapter(store, out_dir=settings.out_dir)
    catalogue = LanguageCatalogue()
    run_job = build_run_job(settings, watch_dir=settings.in_dir, upload_dir=settings.upload_dir)
    worker = JobWorker(store=adapter.notifying_store, run_job=run_job)
    watcher = Watcher(
        settings.in_dir,
        on_ready=lambda path: _enqueue_watch_file(adapter, settings, path),
        known_paths=lambda: _queued_watch_paths(store, settings.in_dir),
    )
    sweeper = RetentionSweeper(
        settings.out_dir,
        retention_days=settings.retention_days,
        upload_dir=settings.upload_dir,
        folder_is_live=lambda folder: folder_is_live(store, folder),
    )
    adapter.attach_health(worker=worker, watcher=watcher)

    has_running_job = lambda: _has_running_job(store)  # noqa: E731
    update_applier = UpdaterAdapter(
        on_applied=lambda: _apply_update_shutdown(
            worker, watcher, sweeper, lock, has_running_job=has_running_job
        ),
        apply=lambda artifact_path, *, has_running_job: apply_update_when_idle(
            artifact_path, has_running_job=has_running_job, watcher=watcher
        ),
    )
    # Optional create_app keywords, passed only where this tree's web layer
    # declares them: the shared-model preview renderer and the updater gate.
    extras: dict = {}
    preview_renderer = _shared_preview_renderer(settings, run_job)
    if preview_renderer is not None:
        extras["preview_renderer"] = preview_renderer
    if updates_supported is not None and "updates_supported" in accepted_kwargs(create_app, ("updates_supported",)):
        extras["updates_supported"] = updates_supported
    app = create_app(
        adapter, catalogue, update_applier=update_applier, incoming_dir=settings.upload_dir, **extras
    )
    app.state.has_running_job = has_running_job
    # Health line for the control page: worker_alive, worker_last_error,
    # current_job_id, watcher_alive, last_watcher_poll.
    app.state.health = adapter.health

    return app, adapter, worker, watcher, sweeper


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    load_warnings: list[str] = []
    settings = Settings.load(on_warning=load_warnings.append)
    try:
        configure_logging(settings.log_path)
    except OSError as exc:
        _message_box(
            f"ASH Captions cannot write its log file at {settings.log_path}: {exc}\n\n"
            "Check that the folder exists and this account can write to it.",
            icon=MB_ICONERROR,
        )
        sys.exit(1)
    for warning in load_warnings:
        logger.warning(warning)

    try:
        _run(args, settings)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - last line of defence for a windowed build
        logger.exception("ASH Captions failed to start or crashed")
        _message_box(
            "ASH Captions hit an error and had to stop.\n\n"
            f"Details are in the log file:\n{settings.log_path}",
            icon=MB_ICONERROR,
        )
        sys.exit(1)


def _run(args: argparse.Namespace, settings: Settings) -> None:
    # Held for the rest of this function's lifetime -- do not let `lock`
    # go out of scope or get closed before shutdown.
    lock_path = settings.db_path.parent / "ash-captions.lock"
    try:
        lock = _acquire_single_instance_lock(lock_path)
    except PermissionError as exc:
        logger.error("Cannot create the single-instance lock at %s: %s", lock_path, exc)
        _message_box(
            f"ASH Captions cannot write to its data folder:\n{lock_path.parent}\n\n"
            "Check that this account has permission to write there.",
            icon=MB_ICONERROR,
        )
        sys.exit(1)
    if lock is None:
        logger.warning("Another ASH Captions instance is already running; exiting.")
        _warn_already_running()
        return

    logger.info("ASH Captions starting (open_browser=%s)", args.open)
    # ffmpeg children die with this process, however it dies (see jobobject.py).
    jobobject.assign_current_process()

    app, _adapter, worker, watcher, sweeper = build_application(settings, lock=lock)

    update_state = UpdateState()
    app.state.update_state = update_state
    _start_update_check(update_state)

    port = _find_open_port(settings.port, max_probes=MAX_PORT_PROBES)
    url = f"http://127.0.0.1:{port}"

    worker.start()
    watcher.start()
    sweeper.start()

    server_thread = threading.Thread(
        target=lambda: run_server(app, port=port), name="ash-captions-web", daemon=True
    )
    server_thread.start()
    logger.info("Control page at %s; health: %s", url, _adapter.health())

    # Bare invocation (the logon task) is tray-only -- no browser tab on
    # every boot. --open (the desktop shortcut / Start Menu entry) also
    # opens the control page.
    if args.open:
        webbrowser.open(url)

    def shutdown() -> None:
        logger.info("ASH Captions shutting down")
        watcher.stop()
        worker.stop()  # cancels any running job (requeued as pending) and waits up to 30 s
        sweeper.stop()
        lock.close()  # release the single-instance lock explicitly, don't wait on process exit

    try:
        from .tray import build_tray_icon

        icon = build_tray_icon(url=url, settings=settings, on_quit=shutdown, update_state=update_state)
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
