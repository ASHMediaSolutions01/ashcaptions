"""Pydantic models shared by the web API.

These models define the contract between the web layer and its injected
dependencies (the job queue and the language catalogue). Neither dependency
is implemented here -- see `interfaces.py` for the protocols the web layer
expects them to satisfy.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

ALLOWED_PRESETS = ("CLEAN", "POP")

# Extensions accepted at the API boundary. This is a coarse, fast check --
# the real "is this actually a readable video" check happens in the engine
# via ffprobe. This just keeps obviously-wrong files (images, docs, audio-only
# maybe not) out of the queue with an immediate, friendly error.
ALLOWED_VIDEO_EXTENSIONS = (
    ".mp4",
    ".mov",
    ".mkv",
    ".mxf",
    ".avi",
    ".webm",
    ".m4v",
)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobOptions(BaseModel):
    """User-selected options for a single job, as submitted from the control page."""

    language: str = Field(..., min_length=1, description="Whisper language code, e.g. 'en'")
    dialect: str | None = Field(None, description="Dialect preset code, e.g. 'en-US'")
    preset: str = Field(..., description="Caption style preset: CLEAN or POP")
    burn_in: bool = False
    translate_to_english: bool = False


class JobCreateRequest(BaseModel):
    """Body of POST /api/jobs.

    `file_path` is a path already on local disk (the browser cannot hand us a
    real filesystem path from a file picker for security reasons, so the
    control page uploads the file first -- see `app.py` -- and this model is
    also usable directly by anything that already has a path, such as the
    folder watcher's own submission code path).
    """

    file_path: str
    options: JobOptions


class Job(BaseModel):
    """A single queue entry, as returned to the browser."""

    id: str
    filename: str
    status: JobStatus
    progress: float = Field(0.0, ge=0.0, le=1.0)
    options: JobOptions
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobList(BaseModel):
    jobs: list[Job]


class Dialect(BaseModel):
    code: str
    label: str


class Language(BaseModel):
    code: str
    label: str
    band: str  # "flagship" | "strong" | "works"
    dialects: list[Dialect] = Field(default_factory=list)


class LanguageCatalogue(BaseModel):
    languages: list[Language]
