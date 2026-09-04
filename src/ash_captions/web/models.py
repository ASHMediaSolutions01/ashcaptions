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
    ".wmv",
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
    behind_speaker: bool = Field(False, description="Draw the captions behind the person (reels; slower)")
    client: str | None = Field(
        None,
        description=(
            "Which client the footage is for, e.g. 'Acme'. Picks that client's glossary on top "
            "of the shared one. Sanitized by `validation.validate_client_name`; None = no client."
        ),
    )


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
    behind_speaker: bool = False
    client: str | None = None


class Job(BaseModel):
    """A single queue entry, as returned to the browser.

    `stage`/`stage_started_at`/`started_at` are optional because the queue
    implementation may predate them: the control page derives a stage
    label from `progress` when `stage` is None, and times "Running for…"
    from `created_at` when `started_at` is None. `input_path`/`output_dir`
    back the Review routes (`routes_review.py`); when absent those routes
    answer 404 rather than guessing.
    """

    id: str
    filename: str
    status: JobStatus
    progress: float = Field(0.0, ge=0.0, le=1.0)
    options: JobOptions
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    stage: str | None = Field(
        None, description="extract | transcribe | translate | postprocess | write | burn, while running"
    )
    stage_started_at: datetime | None = None
    input_path: str | None = None
    output_dir: str | None = None


class JobList(BaseModel):
    jobs: list[Job]


class QueueHealth(BaseModel):
    """Liveness of the parts of the app the browser can't see (GET
    /api/health, and the `health` SSE event). Every field is optional so a
    queue that doesn't report a signal yet degrades to "unknown" on the
    page rather than an error."""

    worker_alive: bool | None = None
    last_watcher_poll: datetime | None = None
    server_time: datetime


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
    customized_locally: bool = Field(
        False,
        description=(
            "True when `shipped` is also True AND a user override file exists for this name -- i.e. "
            "`definition` is NOT the pristine built-in any more. Every job (including the watch-folder "
            "default) that uses this name picks up the customized version, not the original, so this "
            "must be surfaced distinctly from plain `shipped` rather than silently folded into it."
        ),
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
    passes), so this is job-shaped rather than a blocking response.

    `phase` is a finer-grained status than `status` alone while `status`
    is "running" -- "transcribing" or "rendering" -- so the editor sees
    real progress instead of a silent multi-second wait (spec 7A.3).
    """

    id: str
    status: PreviewStatus
    phase: str | None = None
    error: str | None = None
    clip_path: str | None = None


# --- In-app updates (spec 11.4) ---------------------------------------------
#
# `app.updater.UpdateInfo` carries more than the browser needs (download_url,
# sha256, the raw manifest) -- this is the trimmed, display-safe subset for
# GET /api/update. The apply flow is job-shaped like preview rendering: a
# download of a multi-hundred-MB installer plus verification takes real
# time, so POST /api/update/apply returns a job handle immediately rather
# than blocking, and the browser polls for progress.


class UpdateAvailable(BaseModel):
    """What GET /api/update returns when a newer version has been found."""

    version: str
    notes: str | None = None
    size_bytes: int
    blocked_reason: str | None = Field(
        None,
        description=(
            "Non-null while applying would currently be refused (e.g. a caption job is still "
            "running) -- the control page should disable its Update button and show this text "
            "rather than let the editor click and get rejected."
        ),
    )


class UpdateApplyStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"


class UpdateApplyJob(BaseModel):
    """Progress of one update-apply attempt. `status == "done"` means the
    verified update was extracted and handed off to the restart helper --
    the app is about to exit and relaunch itself, so the page should expect
    the connection to drop rather than treat that as an error."""

    id: str
    status: UpdateApplyStatus
    error: str | None = None


# --- Studio page ------------------------------------------------------------
#
# The Studio page (/studio/{job_id}) plays a finished job with its captions
# drawn live in the browser and lets the editor click through looks before
# burning one in. Its two mutating routes share one tiny body.


class PresetRequest(BaseModel):
    """Body of POST /api/jobs/{id}/restyle and POST /api/jobs/{id}/burn: the
    name of the look to apply, exactly as listed by GET /api/styles."""

    preset: str = Field(..., min_length=1)


class ClientGlossary(BaseModel):
    """GET /api/clients/{client}/glossary: one client's glossary file.
    `text` is the whole file (empty when it doesn't exist yet); `slug` is
    the file stem it lives under (`<glossary_dir>/<slug>.txt`)."""

    client: str
    slug: str
    text: str


class GlossaryTextRequest(BaseModel):
    """Body of PUT /api/clients/{client}/glossary: the whole file, one
    entry per line -- `wrong spelling => Right Spelling`, or a bare term
    to force its spelling (see `ash_captions.languages.glossary`)."""

    text: str = Field(..., max_length=200_000)


class FontFile(BaseModel):
    """One bundled font file the browser caption renderer can fetch (GET
    /api/fonts/files). `family` is the face name a style's `font` field
    uses -- the name libass matches on; `url` is where GET
    /api/fonts/file/{filename} serves the file."""

    family: str
    url: str
