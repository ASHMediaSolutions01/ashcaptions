"""The app-side half of applying an update (spec 11.4): what has to be
true about the queue before ``updater.apply_update`` may hand off to its
detached helper, and how the process gets out of the way afterwards.

Order of events after an editor clicks "Update now":

1. ``apply_update_when_idle`` (injected into ``web.UpdaterAdapter`` as its
   ``apply`` seam) stops the watcher -- nothing new may be picked up from
   ``in\\`` from here on -- then waits, with no deadline, for any running
   job to finish. The studio's jobs run 60-90 minutes; an update simply
   waits for them, logging every five minutes so the wait is visible.
2. Only then does ``updater.apply_update`` extract and spawn the helper.
3. ``apply_update_shutdown`` (the adapter's ``on_applied``) stops the
   worker and exits. A watchdog is armed around that teardown -- but only
   now, with the queue already idle, so it bounds a *teardown*, never a
   job.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import IO, Callable

from ash_captions.pipeline import JobWorker, Watcher

from . import updater
from .lifecycle import RetentionSweeper

logger = logging.getLogger("ash_captions.app")

# How often to re-check for a running job while an update waits, and how
# often to say so in the log.
IDLE_POLL_SECONDS = 30.0
IDLE_LOG_EVERY_SECONDS = 5 * 60

# Bounds the graceful update-apply *teardown* (worker/watcher/sweeper stop
# plus lock release), which only begins once the queue is idle -- see the
# module docstring. Generous because ``JobWorker.stop(timeout=None)`` may
# still have to wait out a job that slipped into the residual window
# between the idle check and the stop (it is cancelled, but the engine
# polls the cancel flag per segment, and a burn-in is killed only once
# ffmpeg notices). Six hours is the same figure as
# ``updater._HELPER_WAIT_DEADLINE_SECONDS``; the two are both "something
# is badly wedged" backstops, not job budgets.
UPDATE_SHUTDOWN_WATCHDOG_SECONDS = 6 * 3600

HasRunningJob = Callable[[], bool]


def wait_until_idle(
    has_running_job: HasRunningJob,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_seconds: float = IDLE_POLL_SECONDS,
    log_every_seconds: float = IDLE_LOG_EVERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Block until ``has_running_job()`` is False. No deadline; logs
    progress every ``log_every_seconds``. Returns seconds waited."""
    started = clock()
    last_log = started
    if has_running_job():
        logger.info("Update apply is waiting for the running caption job to finish...")
    while has_running_job():
        sleep_fn(poll_seconds)
        now = clock()
        if now - last_log >= log_every_seconds:
            last_log = now
            logger.info(
                "Still waiting for the running caption job before applying the update (%d min so far)",
                int((now - started) // 60),
            )
    return clock() - started


def apply_update_when_idle(
    artifact_path: Path,
    *,
    has_running_job: HasRunningJob,
    watcher: Watcher,
    sleep_fn: Callable[[float], None] = time.sleep,
    apply: Callable[..., None] = updater.apply_update,
) -> None:
    """``UpdaterAdapter``'s ``apply`` seam: stop the watcher, wait for the
    queue to go idle, then run the real ``updater.apply_update``. If that
    refuses or fails, the watcher is restarted so drops keep working."""
    logger.info("Update apply requested; stopping the watch folder first")
    watcher.stop()
    try:
        waited = wait_until_idle(has_running_job, sleep_fn=sleep_fn)
        if waited:
            logger.info("Queue idle after %.0f s; applying the update", waited)
        apply(artifact_path, has_running_job=has_running_job)
    except BaseException:
        try:
            watcher.start()
        except Exception:  # noqa: BLE001 - the original failure is the one to surface
            logger.exception("Could not restart the watch folder after a failed update apply")
        raise


def shutdown_with_watchdog(shutdown_fn: Callable[[], None], *, timeout: float) -> None:
    """Run ``shutdown_fn`` with a last-resort forced exit if it never
    returns. An update that leaves the app wedged and un-restartable is
    worse than one that exits abruptly after a very generous wait --
    see ``UPDATE_SHUTDOWN_WATCHDOG_SECONDS``.

    The watchdog timer is cancelled in a ``finally`` the moment
    ``shutdown_fn`` returns (or raises) -- this is not optional
    housekeeping. A live, uncancelled ``threading.Timer`` holding a real
    ``os._exit`` call is a timer bomb: in the real path ``shutdown_fn``
    (``apply_update_shutdown``) itself calls ``os._exit()`` at the end, so
    an uncancelled timer looked harmless there -- the process was already
    gone before it could matter. But this function's own contract allows
    ``shutdown_fn`` to simply return instead, and anything that does would
    leave a live timer armed to kill a perfectly healthy process
    ``timeout`` seconds later, from *outside* the call that created it,
    with exit code 1 and nothing in the logs to explain why. (This
    happened for real, here, in this project's own test suite -- see the
    fixed version of ``TestShutdownWithWatchdog`` for the postmortem.)
    """
    watchdog = threading.Timer(timeout, lambda: os._exit(1))
    watchdog.daemon = True
    watchdog.start()
    try:
        shutdown_fn()
    finally:
        watchdog.cancel()


def apply_update_shutdown(
    worker: JobWorker,
    watcher: Watcher,
    sweeper: RetentionSweeper,
    lock: IO[str] | None,
    *,
    has_running_job: HasRunningJob | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """The graceful half of applying an update (spec 11.4) -- passed as
    ``UpdaterAdapter(on_applied=...)`` so ``apply_update()``'s detached
    helper only ever robocopies over a cleanly-stopped app, never one
    frozen mid-transcode.

    Deliberately different from a normal Quit: any job still running (one
    that slipped past every idle check) is waited for -- first without a
    watchdog at all, via ``wait_until_idle``, then ``worker.stop(timeout=None)``
    blocks for real until the loop exits. Only that final teardown sits
    under ``shutdown_with_watchdog``. Always ends the process itself, on
    every path -- a teardown error must not leave a half-shut-down app
    sitting there instead of either finishing the update or plainly
    failing to start again.
    """
    if has_running_job is not None:
        wait_until_idle(has_running_job, sleep_fn=sleep_fn)

    def _teardown_and_exit() -> None:
        try:
            logger.info(
                "Shutting down for update apply (waiting for any in-flight job to finish)..."
            )
            watcher.stop()
            worker.stop(timeout=None)
            sweeper.stop()
            if lock is not None:
                lock.close()
        except Exception:  # noqa: BLE001 - still exit even if teardown itself misbehaves
            logger.exception("Error during update-apply shutdown; exiting anyway")
        finally:
            # A moment for the "apply status: done" HTTP response the
            # editor's click is waiting on to actually reach the browser
            # before the server that would serve it disappears.
            time.sleep(1.5)
            os._exit(0)

    shutdown_with_watchdog(_teardown_and_exit, timeout=UPDATE_SHUTDOWN_WATCHDOG_SECONDS)
