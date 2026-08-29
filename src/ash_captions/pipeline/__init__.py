"""Durable job queue and folder watcher for the captioning pipeline.

Public surface:

- ``db``: SQLite-backed job storage (``JobStore``, ``Job``, ``JobOptions``,
  ``JobStatus``).
- ``queue``: single background worker that drains pending jobs
  (``JobWorker``), given a caller-supplied ``run_job`` callable -- this
  package never imports the transcription engine.
- ``watcher``: watch-folder detection for ``in\\`` that only reports a file
  once it has stopped changing and the OS has released its handle
  (``Watcher``).
"""

from .db import Job, JobOptions, JobStatus, JobStore
from .queue import JobWorker, ProgressCallback, RunJob
from .watcher import Watcher, is_eligible

__all__ = [
    "Job",
    "JobOptions",
    "JobStatus",
    "JobStore",
    "JobWorker",
    "ProgressCallback",
    "RunJob",
    "Watcher",
    "is_eligible",
]
