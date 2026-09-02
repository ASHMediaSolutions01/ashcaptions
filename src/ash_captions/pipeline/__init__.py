"""Durable job queue and folder watcher for the captioning pipeline.

Public surface:

- ``db``: SQLite-backed job storage (``JobStore``, ``Job``, ``JobOptions``,
  ``JobStatus``).
- ``queue``: single background worker that drains pending jobs
  (``JobWorker``), given a caller-supplied ``run_job`` callable -- this
  package never imports the transcription engine. ``run_job`` receives a
  ``ProgressReporter`` and may raise ``JobCancelled`` when asked to stop.
- ``watcher``: watch-folder detection for ``in\\`` that only reports a file
  once it has stopped changing and the OS has released its handle
  (``Watcher``).
"""

from .db import DuplicateJobError, Job, JobOptions, JobStatus, JobStore
from .queue import AfterDone, JobCancelled, JobWorker, ProgressCallback, ProgressReporter, RunJob
from .watcher import Watcher, is_eligible

__all__ = [
    "AfterDone",
    "DuplicateJobError",
    "Job",
    "JobCancelled",
    "JobOptions",
    "JobStatus",
    "JobStore",
    "JobWorker",
    "ProgressCallback",
    "ProgressReporter",
    "RunJob",
    "Watcher",
    "is_eligible",
]
