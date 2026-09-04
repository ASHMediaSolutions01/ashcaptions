"""Bridges web's ``JobQueue`` protocol onto ``pipeline.JobStore`` (spec
section 8).

Four real impedance mismatches sit between the two layers:

1. **Id type** -- web's ``Job.id`` is ``str``; pipeline's is a SQLite
   ``int``. Converted both ways; a non-numeric id is treated as "not
   found" rather than letting ``ValueError`` escape.
2. **Progress scale** -- web's ``Job.progress`` is ``0.0-1.0``; pipeline's
   ``mark_progress`` takes ``0-100``. Converted and clamped so a rounding
   quirk can never produce a value Pydantic rejects.
3. **Options shape** -- web's ``JobOptions`` is a Pydantic model;
   pipeline's is a frozen dataclass. Mapped field by field.
4. **Filename** -- pipeline's ``Job`` has no ``filename``; it is derived
   from the input path's basename.

Push-driven SSE
----------------
``subscribe()`` must never poll (spec section 8.3). Each subscriber gets
its own ``asyncio.Queue``; ``notify()`` (every state change -- see
``adapter_store.NotifyingStore``) pushes a fresh snapshot into each. The worker thread
runs outside the event loop, so publishing is marshalled back with
``loop.call_soon_threadsafe`` (``asyncio.Queue.put`` from a foreign thread
deadlocks or corrupts the queue). Publishing is throttled to one snapshot
per ``notify_interval`` (trailing edge, so the last value always lands):
ffmpeg reports several times a second and each publish is a ``SELECT``
plus a JSON encode per tab. ``mark_progress`` also skips the write when
the integer percentage hasn't changed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ash_captions import engine, styles
from ash_captions.config import Settings
from ash_captions.pipeline.db import DuplicateJobError
from ash_captions.pipeline.db import Job as PipelineJob
from ash_captions.pipeline.db import JobOptions as PipelineJobOptions
from ash_captions.pipeline.db import JobStatus as PipelineJobStatus
from ash_captions.pipeline.db import JobStore
from ash_captions.web.interfaces import JobNotFoundError, JobNotRemovableError, JobNotRetryableError
from ash_captions.web.models import Job as WebJob
from ash_captions.web.models import JobOptions as WebJobOptions
from ash_captions.web.models import JobStatus as WebJobStatus

from .adapter_store import NotifyingStore
from .runner_util import atomic_write, client_for_watch_path
from .transcript import TranscriptError, TranscriptRecord, load_transcript, transcript_path

logger = logging.getLogger("ash_captions.app.adapter")

# The control page never needs the whole history; the newest rows are
# what an editor is looking at.
DEFAULT_LIST_LIMIT = 200
DEFAULT_NOTIFY_INTERVAL_SECONDS = 1.0

# Extra web Job fields, set only when the model declares them (pydantic
# would silently drop unknown kwargs and hide a wiring gap).
_OPTIONAL_WEB_FIELDS = ("stage", "stage_started_at", "started_at", "input_path", "output_dir")

_SUFFIX_RE = re.compile(r"^(.*) \((\d+)\)$")


class QueueAdapter:
    """Implements web's ``JobQueue`` protocol over ``pipeline.JobStore``.

    ``out_dir`` is the output root (``settings.out_dir``); each job gets
    its own ``out_dir/<video stem>`` subfolder (spec section 10), made
    unique (``<stem> (2)``...) against the disk and every job row.
    ``watch_dir`` (``settings.in_dir``) lets ``submit`` name the client for
    a watch-folder drop from its subfolder (``in\\Acme\\clip.mp4`` ->
    "Acme") when the options carry none; unset, it is read from
    ``Settings.load()`` on first use.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        out_dir: Path,
        notify_interval: float = DEFAULT_NOTIFY_INTERVAL_SECONDS,
        list_limit: int = DEFAULT_LIST_LIMIT,
        watch_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._out_dir = Path(out_dir)
        self._watch_dir = Path(watch_dir) if watch_dir is not None else None
        self._notify_interval = notify_interval
        self._list_limit = list_limit
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._last_publish: float = 0.0
        self._trailing_handle: asyncio.TimerHandle | None = None
        self._health_sources: dict[str, Any] = {}
        self._settings: Settings | None = None  # lazily loaded for restyle card rules
        # Called with the finished web ``Job`` when the worker marks one
        # done/failed (the tray's balloon subscribes here). Worker thread;
        # a failing subscriber is logged, never allowed to kill the worker.
        self.on_job_finished: list[Callable[[WebJob], None]] = []
        # Handed to JobWorker in place of the raw store (see module
        # docstring); a long-lived object JobWorker holds onto.
        self.notifying_store = NotifyingStore(store, self._notify, self._fire_finished)

    # -- JobQueue protocol -----------------------------------------------

    def list_jobs(self) -> list[WebJob]:
        # pipeline.JobStore.list_jobs() already orders newest-first.
        return [_to_web_job(job) for job in self._store.list_jobs(limit=self._list_limit)]

    def get_job(self, job_id: str) -> WebJob | None:
        numeric_id = _parse_job_id(job_id)
        if numeric_id is None:
            return None
        job = self._store.get_job(numeric_id)
        return _to_web_job(job) if job is not None else None

    def submit(self, file_path: Path, options: WebJobOptions) -> WebJob:
        """Enqueue ``file_path``. A file that already has a ``pending`` or
        ``running`` job is not queued again; its existing job is returned."""
        self._capture_loop()
        file_path = Path(file_path)
        existing = self._store.find_live_job(file_path)
        if existing is not None:
            return _to_web_job(existing)
        output_dir = self.unique_output_dir(file_path.stem)
        pipeline_options = _to_pipeline_options(options)
        if pipeline_options.client is None:
            # A drop into in\<Client>\ names its client by the folder.
            derived = client_for_watch_path(file_path, self._resolve_watch_dir())
            if derived is not None:
                pipeline_options = dataclasses.replace(pipeline_options, client=derived)
        job_id = self._store.insert_job(file_path, output_dir, pipeline_options)
        job = self._store.get_job(job_id)
        assert job is not None, "insert_job returned an id that get_job can't find"
        self._notify()
        return _to_web_job(job)

    def known_clients(self) -> list[str]:
        """Distinct clients on recent jobs, most recent first (the client picker)."""
        return self._store.known_clients()

    def remove_job(self, job_id: str) -> None:
        """Forget a finished job's row ("Remove from list"); its files stay.
        Raises ``JobNotFoundError`` / ``JobNotRemovableError`` (still live)."""
        self._capture_loop()
        numeric_id = _parse_job_id(job_id)
        if numeric_id is None:
            raise JobNotFoundError(job_id)
        try:
            self._store.delete_job(numeric_id)
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc
        except ValueError as exc:
            raise JobNotRemovableError(str(exc)) from exc
        self._notify()

    def _fire_finished(self, job_id: int) -> None:  # worker thread
        job = self._store.get_job(job_id) if self.on_job_finished else None
        if job is None:
            return
        for callback in list(self.on_job_finished):
            try:
                callback(_to_web_job(job))
            except Exception:  # noqa: BLE001 - a notifier must never take the worker down
                logger.exception("on_job_finished subscriber failed for job %s", job_id)

    def _resolve_watch_dir(self) -> Path:
        if self._watch_dir is None:
            self._watch_dir = Path(self._load_settings().in_dir)
        return self._watch_dir

    def unique_output_dir(self, stem: str) -> Path:
        """``out_dir/<stem>``, or the first ``<stem> (n)`` not yet used on
        disk or by any job row."""
        candidate = self._out_dir / stem
        n = 1
        while candidate.exists() or self._store.output_dir_in_use(candidate):
            n += 1
            candidate = self._out_dir / f"{stem} ({n})"
        return candidate

    # -- Studio (v0.3): re-style and burn from the saved transcript ---------

    def restyle(self, job_id: str, preset: str) -> WebJob:
        """Re-render the job's ``.ass`` in ``preset`` from its saved transcript,
        in place, and record the new preset on the row. Seconds, not
        minutes: nothing is transcribed. Raises ``JobNotFoundError`` for an
        unknown job and ``ValueError`` when there is no usable transcript or
        the preset is not a known style."""
        job = self._require_job(job_id)
        record = self._transcript_for(job)
        if not preset in styles.list_styles():
            raise ValueError(f"Unknown caption style {preset!r}")
        style = styles.resolve_style(preset)
        stem = Path(job.input_path).stem
        max_words = style.layout.max_words
        cards = engine.build_cards(
            record.words,
            max_words=max_words,
            min_words=min(3, max_words),
            silence_gap=self._silence_gap_seconds(),
        )
        optional = {"play_res": record.play_res} if record.play_res else {}
        atomic_write(
            lambda p: engine.write_ass(cards, p, style, **optional),
            Path(job.output_dir) / f"{stem}.ass",
        )
        new_options = dataclasses.replace(job.options, preset=style.name)
        self._store.update_options(job.id, new_options)
        updated = self._store.get_job(job.id)
        assert updated is not None
        self._notify()
        return _to_web_job(updated)

    def submit_burn(self, job_id: str, preset: str) -> WebJob:
        """Enqueue a burn-only job for the same input, in ``preset``, into the
        same output folder. Reuses the saved transcript; fails at run time
        if the input has since changed."""
        self._capture_loop()
        job = self._require_job(job_id)
        self._transcript_for(job)  # raise now, not an hour later in the worker
        if not preset in styles.list_styles():
            raise ValueError(f"Unknown caption style {preset!r}")
        if not Path(job.input_path).is_file():
            raise ValueError("The original video is no longer where it was, so it cannot be burned.")
        options = dataclasses.replace(job.options, preset=styles.resolve_style(preset).name, burn=True, mode="burn_only")
        new_id = self._store.insert_job(job.input_path, job.output_dir, options)
        created = self._store.get_job(new_id)
        assert created is not None
        self._notify()
        return _to_web_job(created)

    def submit_translate(self, job_id: str) -> WebJob:
        """Enqueue a translate-only job for the same input into the same
        output folder (v0.5 caption check): the runner reuses the saved
        transcript, runs only the English pass, adds ``en_words`` and
        writes ``<stem>.en.srt``. Raises ``JobNotFoundError``, or
        ``ValueError`` when there is no usable transcript or the input
        file is gone. A translate already queued for this file is
        returned instead of a duplicate (``insert_job``'s live-row rule)."""
        self._capture_loop()
        job = self._require_job(job_id)
        self._transcript_for(job)  # raise now, not minutes later in the worker
        if not Path(job.input_path).is_file():
            raise ValueError("The original video is no longer where it was, so it cannot be translated.")
        options = dataclasses.replace(
            job.options, translate=True, burn=False, behind_speaker=False, mode="translate_only"
        )
        new_id = self._store.insert_job(job.input_path, job.output_dir, options)
        created = self._store.get_job(new_id)
        assert created is not None
        self._notify()
        return _to_web_job(created)

    def _require_job(self, job_id: str) -> PipelineJob:
        numeric_id = _parse_job_id(job_id)
        job = self._store.get_job(numeric_id) if numeric_id is not None else None
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def _transcript_for(self, job: PipelineJob) -> TranscriptRecord:
        stem = Path(job.input_path).stem
        try:
            record = load_transcript(transcript_path(Path(job.output_dir), stem))
        except TranscriptError as exc:
            raise ValueError(
                "This job has no saved transcript (it ran before the Studio existed, or the "
                "transcript file was removed). Run it again to get one."
            ) from exc
        if not record.words:
            raise ValueError("The saved transcript has no words to caption.")
        return record

    def _load_settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings.load()
        return self._settings

    def _silence_gap_seconds(self) -> float:
        return float(self._load_settings().silence_gap_seconds)

    def retry(self, job_id: str) -> WebJob:
        self._capture_loop()
        numeric_id = _parse_job_id(job_id)
        if numeric_id is None:
            raise JobNotFoundError(job_id)
        job = self._store.get_job(numeric_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != PipelineJobStatus.FAILED:
            raise JobNotRetryableError(job_id)

        try:
            self._store.requeue(numeric_id)
        except DuplicateJobError as exc:
            raise JobNotRetryableError(str(exc)) from exc

        updated = self._store.get_job(numeric_id)
        assert updated is not None, "job disappeared between requeue and re-fetch"
        self._notify()
        return _to_web_job(updated)

    async def subscribe(self) -> AsyncIterator[list[WebJob]]:
        self._capture_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield self.list_jobs()
            while True:
                snapshot = await queue.get()
                yield snapshot
        finally:
            self._subscribers.discard(queue)

    # -- health (read by the control page, outside the JobQueue protocol) --

    def attach_health(self, *, worker: Any = None, watcher: Any = None) -> None:
        """Give ``health()`` the worker/watcher to report on."""
        self._health_sources = {"worker": worker, "watcher": watcher}

    def health(self) -> dict[str, Any]:
        """Plain dict for a status line: ``worker_alive``,
        ``worker_last_error``, ``last_watcher_poll`` (ISO-8601 or None)."""
        worker = self._health_sources.get("worker")
        watcher = self._health_sources.get("watcher")
        last_poll = getattr(watcher, "last_poll_at", None) if watcher is not None else None
        return {
            "worker_alive": bool(worker.is_alive()) if worker is not None else False,
            "worker_last_error": getattr(worker, "last_error", None) if worker is not None else None,
            "current_job_id": getattr(worker, "current_job_id", None) if worker is not None else None,
            "watcher_alive": bool(watcher.is_alive()) if watcher is not None else False,
            "last_watcher_poll": _iso_or_none(last_poll),
        }

    # -- push plumbing -----------------------------------------------------

    def _capture_loop(self) -> None:
        """Remember the running event loop the first time we're called from
        one (``submit``/``retry`` run inside an ``async def`` route handler,
        ``subscribe`` is a coroutine -- all on the loop thread)."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def _notify(self) -> None:
        """Wake every subscriber with a fresh snapshot (rate-limited).

        Safe from any thread: with no loop captured yet this is a no-op
        (nothing to wake; the next subscriber's first snapshot is current).
        Once a loop is known, publishing goes through
        ``call_soon_threadsafe`` -- the only safe way to touch an
        ``asyncio.Queue`` from the worker thread (spec section 8.3)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._schedule_publish)
        except RuntimeError:
            # Loop shut down between the check and the call (app exiting).
            return

    def _schedule_publish(self) -> None:
        """On the loop thread: publish now if the interval has elapsed,
        otherwise arm one trailing publish for when it does."""
        if self._trailing_handle is not None:
            return  # a trailing publish is already armed; it will carry this change
        elapsed = time.monotonic() - self._last_publish
        if elapsed >= self._notify_interval:
            self._publish()
            return
        loop = self._loop
        assert loop is not None
        self._trailing_handle = loop.call_later(self._notify_interval - elapsed, self._publish_trailing)

    def _publish_trailing(self) -> None:
        self._trailing_handle = None
        self._publish()

    def _publish(self) -> None:
        self._last_publish = time.monotonic()
        if not self._subscribers:
            return
        snapshot = self.list_jobs()
        for subscriber in list(self._subscribers):
            subscriber.put_nowait(snapshot)


# -- conversions -------------------------------------------------------------


def _parse_job_id(job_id: str) -> int | None:
    try:
        return int(job_id)
    except (TypeError, ValueError):
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _iso_or_none(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _to_web_options(options: PipelineJobOptions) -> WebJobOptions:
    return WebJobOptions(
        language=options.language,
        dialect=options.dialect,
        preset=options.preset,
        burn_in=options.burn,
        translate_to_english=options.translate,
        client=getattr(options, "client", None),
        behind_speaker=bool(getattr(options, "behind_speaker", False)),
    )


def _to_pipeline_options(options: WebJobOptions) -> PipelineJobOptions:
    return PipelineJobOptions(
        language=options.language,
        dialect=options.dialect,
        preset=options.preset,
        burn=options.burn_in,
        translate=options.translate_to_english,
        client=getattr(options, "client", None),
        behind_speaker=bool(getattr(options, "behind_speaker", False)),
    )


def _to_web_job(job: PipelineJob) -> WebJob:
    # pipeline.Job has no "last touched" stamp; the newest of these is the closest.
    updated_raw = job.finished_at or job.stage_started_at or job.started_at or job.created_at
    extras = {
        "stage": job.stage,
        "stage_started_at": job.stage_started_at,
        "started_at": job.started_at,
        "input_path": job.input_path,
        "output_dir": job.output_dir,
    }
    declared = getattr(WebJob, "model_fields", {})
    optional = {name: extras[name] for name in _OPTIONAL_WEB_FIELDS if name in declared}
    return WebJob(
        id=str(job.id),
        filename=Path(job.input_path).name,
        status=WebJobStatus(job.status.value),
        progress=_clamp01(job.progress / 100.0),
        options=_to_web_options(job.options),
        error=job.error,
        created_at=datetime.fromisoformat(job.created_at),
        updated_at=datetime.fromisoformat(updated_raw),
        **optional,
    )
