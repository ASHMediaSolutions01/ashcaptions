"""Application wiring: the seam between the engine, languages, pipeline and
web packages, plus process lifecycle (spec sections 8-12).

Public surface:

- ``adapter``: ``QueueAdapter``, implementing web's ``JobQueue`` protocol
  over ``pipeline.JobStore``.
- ``catalogue``: ``LanguageCatalogue``, implementing web's
  ``LanguageCatalogueProvider`` protocol over ``ash_captions.languages``.
- ``runner``: ``build_run_job``, the pipeline callable ``pipeline.JobWorker``
  executes -- the only place ``engine`` and ``languages`` meet.
- ``lifecycle``: rotating file logging and 30-day output retention.
- ``__main__``: assembly (``build_application``) and the process entry
  point (``main``).
- ``tray``: the pystray tray icon that is this app's actual identity on an
  editor's desktop.
"""

from .adapter import QueueAdapter
from .catalogue import LanguageCatalogue
from .runner import build_run_job

__all__ = [
    "QueueAdapter",
    "LanguageCatalogue",
    "build_run_job",
]
