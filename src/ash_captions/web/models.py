"""Pydantic models shared by the web API.

These models define the contract between the web layer and its injected
dependencies (the job queue and the language catalogue). Neither dependency
is implemented here -- see `interfaces.py` for the protocols the web layer
expects them to satisfy.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Superseded by the live style list (spec 7A): `app.py._validate_options`
# now validates a submitted "preset" against `StyleProvider.list_styles()`
# rather than this fixed pair. Left in place in case anything outside this
# package still imports it.
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


class JobPathRequest(BaseModel):
    """Body of POST /api/jobs/by-path -- the primary submission route.

    The footage already lives on this machine (spec §4.4: one editor, one
    PC), so the normal path is "point at the file", not "upload a copy of
    it to yourself". `path` may be wrapped in quotes (Windows Explorer's
    "Copy as path" does this); `app.py` strips them before validating.
    """

    path: str
    language: str = Field(..., min_length=1)
    dialect: str | None = None
    preset: str
    burn_in: bool = False
    translate_to_english: bool = False


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


# --- Caption styling (spec 7A) ---------------------------------------------
#
# A style's wire shape is exactly `ash_captions.styles.Style.to_dict()`
# (spec 7A.2's JSON example): name, font, size, uppercase, letter_spacing,
# colors, active_word, entrance, exit, layout. The web layer treats that as
# opaque data it round-trips to the styles package for validation/rendering
# -- "styles are data" (spec 7A.2) applies here too, so it is modelled as a
# plain dict rather than re-declared field by field and risking drift from
# the one real schema in `ash_captions.styles.schema`.


class StyleSummary(BaseModel):
    """One style, as returned by GET /api/styles and GET /api/styles/{name}."""

    name: str
    shipped: bool = Field(
        ..., description="True for one of the built-in looks; the editor may edit it but never delete it."
    )
    definition: dict[str, Any]


class PreviewRequest(BaseModel):
    """Body of POST /api/styles/preview (spec 7A.3)."""

    video_path: str
    start_seconds: float = Field(..., ge=0.0)
    style: dict[str, Any] = Field(..., description="An in-progress style edit, same shape as StyleSummary.definition")


class PreviewStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class PreviewJob(BaseModel):
    """A style preview render, polled from the browser while it runs --
    rendering a ~3s clip takes real seconds (transcription + two ffmpeg
    passes), so this is job-shaped rather than a blocking response."""

    id: str
    status: PreviewStatus
    error: str | None = None
    clip_path: str | None = None
