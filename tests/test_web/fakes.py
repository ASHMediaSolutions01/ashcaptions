"""In-memory fakes for the queue and language catalogue protocols the web
layer depends on (see `ash_captions.web.interfaces`). No SQLite, no ffmpeg,
no model -- just enough behaviour to exercise the API."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from ash_captions.web.interfaces import (
    BundledFontFile,
    BundledSound,
    JobNotFoundError,
    JobNotRemovableError,
    JobNotRetryableError,
    PreviewNotFoundError,
    StyleIsShippedError,
    StyleNotFoundError,
    StyleValidationFailedError,
)
from ash_captions.web.models import (
    Dialect,
    Job,
    JobOptions,
    JobStatus,
    Language,
    PreviewJob,
    PreviewStatus,
    StyleSummary,
)

# `FakeJobQueue.restyle(position=KEEP)`: the route sent no position keys.
KEEP = object()


class FakeJobQueue:
    """Implements the `JobQueue` protocol in memory.

    `output_root` mirrors `app.adapter.QueueAdapter`'s `out_dir`: each
    submitted job gets `output_root/<stem>` as its `output_dir`, so the
    Review routes can be exercised against real temp files.
    `worker_alive`/`last_watcher_poll` are the optional health attributes
    the web layer probes for (None = "not reported").
    """

    def __init__(
        self,
        jobs: list[Job] | None = None,
        *,
        output_root: Path | None = None,
        known_presets: set[str] | None = None,
    ) -> None:
        self._jobs: dict[str, Job] = {j.id: j for j in (jobs or [])}
        self._subscribers: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._output_root = output_root
        self.worker_alive: bool | None = None
        self.last_watcher_poll: datetime | None = None
        # Studio (restyle/submit_burn): `known_presets` None accepts any
        # name; ids in `no_saved_words` behave like jobs run by an older
        # build that kept no word timings.
        self._known_presets = known_presets
        self.no_saved_words: set[str] = set()
        self.restyled: list[tuple[str, str]] = []
        self.burns: list[Job] = []
        self.translations: list[Job] = []
        # Every `position=` the restyle route forwarded, in order: a pair,
        # or None for "clear". Nothing is appended when the keys were omitted.
        self.positions: list[tuple[float, float] | None] = []

    def list_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: (j.created_at, j.id), reverse=True)

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def submit(self, file_path: Path, options: JobOptions) -> Job:
        now = datetime.now(timezone.utc)
        output_dir = self._output_root / file_path.stem if self._output_root is not None else None
        job = Job(
            id=uuid.uuid4().hex,
            filename=file_path.name,
            status=JobStatus.PENDING,
            progress=0.0,
            options=options,
            error=None,
            created_at=now,
            updated_at=now,
            input_path=str(file_path),
            output_dir=str(output_dir) if output_dir is not None else None,
        )
        self._jobs[job.id] = job
        self._notify()
        return job

    def retry(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.FAILED:
            raise JobNotRetryableError(job_id)
        updated = job.model_copy(
            update={
                "status": JobStatus.PENDING,
                "error": None,
                "progress": 0.0,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._jobs[job_id] = updated
        self._notify()
        return updated

    async def subscribe(self) -> AsyncIterator[list[Job]]:
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            yield self.list_jobs()
            while True:
                snapshot = await queue.get()
                yield snapshot
        finally:
            self._subscribers.remove(queue)

    def _notify(self) -> None:
        """Same marshalling rule as the real `QueueAdapter._notify`: an
        `asyncio.Queue` may only be touched from its own loop's thread, and
        `TestClient` runs the app on a different thread from the test."""
        snapshot = self.list_jobs()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(self._publish, snapshot)
                return
        self._publish(snapshot)

    def _publish(self, snapshot: list[Job]) -> None:
        for subscriber in list(self._subscribers):
            subscriber.put_nowait(snapshot)

    # -- Studio (optional queue extras, see interfaces.JobQueue) --------

    def restyle(self, job_id: str, preset: str, *, position: object = KEEP) -> Job:
        job = self._restylable(job_id, preset)
        if job.output_dir:
            # The real queue rewrites the job's .ass in place; the fake
            # writes a recognisable stand-in so a route test can see the
            # new track being served.
            out = Path(job.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{Path(job.filename).stem}.ass").write_text(
                f"[Script Info]\nTitle: restyled as {preset}\n", encoding="utf-8"
            )
        changes: dict[str, Any] = {"preset": preset}
        if position is not KEEP:
            pair = position if position is None else (float(position[0]), float(position[1]))  # type: ignore[index]
            self.positions.append(pair)
            changes["caption_x"], changes["caption_y"] = (None, None) if pair is None else pair
        updated = job.model_copy(
            update={
                "options": job.options.model_copy(update=changes),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._jobs[job_id] = updated
        self.restyled.append((job_id, preset))
        self._notify()
        return updated

    def submit_burn(self, job_id: str, preset: str) -> Job:
        source = self._restylable(job_id, preset)
        now = datetime.now(timezone.utc)
        burn = Job(
            id=uuid.uuid4().hex,
            filename=source.filename,
            status=JobStatus.PENDING,
            progress=0.0,
            options=source.options.model_copy(update={"preset": preset, "burn_in": True}),
            error=None,
            created_at=now,
            updated_at=now,
            input_path=source.input_path,
            output_dir=source.output_dir,
        )
        self._jobs[burn.id] = burn
        self.burns.append(burn)
        self._notify()
        return burn

    def submit_translate(self, job_id: str) -> Job:
        """Optional queue extra (caption check): a translate-only job for the
        same footage into the same folder."""
        source = self._jobs.get(job_id)
        if source is None:
            raise JobNotFoundError(job_id)
        if job_id in self.no_saved_words:
            raise ValueError(f"Job {job_id!r} has no saved transcript to translate from.")
        now = datetime.now(timezone.utc)
        created = Job(
            id=uuid.uuid4().hex,
            filename=source.filename,
            status=JobStatus.PENDING,
            progress=0.0,
            options=source.options.model_copy(update={"translate_to_english": True, "burn_in": False}),
            error=None,
            created_at=now,
            updated_at=now,
            input_path=source.input_path,
            output_dir=source.output_dir,
        )
        self._jobs[created.id] = created
        self.translations.append(created)
        self._notify()
        return created

    def known_clients(self) -> list[str]:
        """Optional queue extra (see interfaces.JobQueue): distinct clients
        on jobs, newest first, case-insensitively deduplicated."""
        seen: dict[str, str] = {}
        for job in self.list_jobs():
            client = job.options.client
            if client:
                seen.setdefault(client.lower(), client)
        return list(seen.values())

    def remove_job(self, job_id: str) -> None:
        """Optional queue extra: forget a finished row (files untouched)."""
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            raise JobNotRemovableError(job_id)
        del self._jobs[job_id]
        self._notify()

    def _restylable(self, job_id: str, preset: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job_id in self.no_saved_words:
            raise ValueError(
                f"Job {job_id!r} has no saved word timings to restyle from -- it was run by an older version."
            )
        if self._known_presets is not None and preset not in self._known_presets:
            raise ValueError(f"Unknown style {preset!r}.")
        return job

    # Test helper only -- not part of the JobQueue protocol.
    def force_status(self, job_id: str, status: JobStatus, **fields: object) -> Job:
        job = self._jobs[job_id]
        updated = job.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc), **fields}
        )
        self._jobs[job_id] = updated
        self._notify()
        return updated


def default_languages() -> list[Language]:
    return [
        Language(
            code="en",
            label="English",
            band="flagship",
            dialects=[
                Dialect(code="en-US", label="US"),
                Dialect(code="en-UK", label="UK"),
            ],
        ),
        Language(
            code="es",
            label="Spanish",
            band="flagship",
            dialects=[
                Dialect(code="es-MX", label="Mexico"),
                Dialect(code="es-ES", label="Spain"),
            ],
        ),
        Language(
            code="pt",
            label="Portuguese",
            band="flagship",
            dialects=[
                Dialect(code="pt-BR", label="Brazil"),
                Dialect(code="pt-PT", label="Portugal"),
            ],
        ),
        Language(code="fr", label="French", band="flagship", dialects=[]),
    ]


class FakeLanguageCatalogue:
    """Implements the `LanguageCatalogueProvider` protocol in memory."""

    def __init__(self, languages: list[Language] | None = None) -> None:
        self._languages = languages if languages is not None else default_languages()

    def list_languages(self) -> list[Language]:
        return self._languages


def default_style_definition(name: str) -> dict[str, Any]:
    """A minimal but complete style dict, same shape as
    `ash_captions.styles.Style.to_dict()` (spec 7A.2)."""
    return {
        "name": name,
        "font": "Inter",
        "size": 72,
        "uppercase": False,
        "letter_spacing": 0.0,
        "colors": {
            "text": "#FFFFFF",
            "active": "#FFD166",
            "outline": "#000000",
            "shadow": "#00000090",
            "box": "#00000000",
        },
        "active_word": {"effect": "pop", "scale": 1.12, "box": False},
        "entrance": {"effect": "fade", "duration_ms": 120},
        "exit": {"effect": "none", "duration_ms": 0},
        "layout": {"position": "bottom", "max_words": 4, "margin_l": 80, "margin_r": 80, "margin_v": 120},
    }


DEFAULT_SHIPPED_STYLE_NAMES = ("CLEAN", "POP")
DEFAULT_BUNDLED_FONTS = ("Inter", "Montserrat", "Anton", "Bebas Neue")


class FakeStyleProvider:
    """Implements the `StyleProvider` protocol in memory. No filesystem, no
    `ash_captions.styles` import -- validation here is a small, deliberately
    simplified stand-in for the real schema, just enough to exercise the web
    layer's error mapping (StyleValidationFailedError -> 400,
    StyleIsShippedError -> 409, StyleNotFoundError -> 404)."""

    def __init__(
        self,
        *,
        shipped: dict[str, dict[str, Any]] | None = None,
        fonts: tuple[str, ...] = DEFAULT_BUNDLED_FONTS,
        fonts_dir: Path | None = None,
        sounds: tuple[str, ...] = ("pop", "whoosh"),
        sounds_dir: Path | None = None,
    ) -> None:
        self._shipped: dict[str, dict[str, Any]] = shipped if shipped is not None else {
            name: default_style_definition(name) for name in DEFAULT_SHIPPED_STYLE_NAMES
        }
        self._user: dict[str, dict[str, Any]] = {}
        self._fonts = fonts
        # Where `list_font_files()` says each face's file lives -- a test
        # writes real bytes there to exercise the font-file routes. None
        # means "no files": the routes then list nothing.
        self._fonts_dir = fonts_dir
        self._sounds = sounds
        # Same contract as `_fonts_dir`: None means the provider carries
        # no sound library.
        self._sounds_dir = sounds_dir

    def list_styles(self) -> list[StyleSummary]:
        merged = {**self._shipped, **self._user}
        return [
            StyleSummary(
                name=name,
                shipped=name in self._shipped,
                customized_locally=name in self._shipped and name in self._user,
                definition=definition,
            )
            for name, definition in merged.items()
        ]

    def get_style(self, name: str, *, shipped_only: bool = False) -> StyleSummary:
        if shipped_only:
            definition = self._shipped.get(name)
        else:
            definition = self._user.get(name, self._shipped.get(name))
        if definition is None:
            raise StyleNotFoundError(name)
        customized = False if shipped_only else name in self._shipped and name in self._user
        return StyleSummary(name=name, shipped=name in self._shipped, customized_locally=customized, definition=definition)

    def save_style(self, name: str, definition: dict[str, Any]) -> StyleSummary:
        payload = dict(definition)
        payload["name"] = name
        self._validate(payload)
        self._user[name] = payload
        return StyleSummary(
            name=name,
            shipped=name in self._shipped,
            customized_locally=name in self._shipped,  # just wrote the user file ourselves
            definition=payload,
        )

    def reset_style(self, name: str) -> StyleSummary:
        if name not in self._shipped:
            raise StyleNotFoundError(name)
        self._user.pop(name, None)
        return self.get_style(name, shipped_only=True)

    def delete_style(self, name: str) -> None:
        if name in self._shipped:
            raise StyleIsShippedError(name)
        if name not in self._user:
            raise StyleNotFoundError(name)
        del self._user[name]

    def list_fonts(self) -> list[str]:
        return list(self._fonts)

    def list_font_files(self) -> list[BundledFontFile]:
        if self._fonts_dir is None:
            return []
        return [
            BundledFontFile(family=family, path=self._fonts_dir / f"{family.replace(' ', '')}-Regular.ttf")
            for family in self._fonts
        ]

    def list_sounds(self) -> list[BundledSound]:
        # None means "this provider has no sound library at all" -- the
        # shape of a bundle built before v0.7, which the routes must
        # answer with an empty list rather than a 500.
        if self._sounds_dir is None:
            return []
        return [
            BundledSound(
                name=name, label=name.title(), description=f"The {name}.",
                duration_seconds=0.2, path=self._sounds_dir / f"{name}.wav",
            )
            for name in self._sounds
        ]

    def _validate(self, definition: dict[str, Any]) -> None:
        if not definition.get("name", "").strip():
            raise StyleValidationFailedError("name: a non-empty style name is required")
        font = definition.get("font")
        if font is not None and font not in self._fonts:
            raise StyleValidationFailedError(
                f"font: {font!r} is not a bundled font -- see assets/fonts/manifest.json for the available faces"
            )
        size = definition.get("size")
        if size is not None and not (10 <= size <= 300):
            raise StyleValidationFailedError(f"size: {size} is out of range (expected 10-300)")
        active_word = definition.get("active_word") or {}
        effect = active_word.get("effect")
        known_effects = {"none", "pop", "box", "scale_box", "card_box", "karaoke", "shake", "glow"}
        if effect is not None and effect not in known_effects:
            raise StyleValidationFailedError(
                f"active_word.effect: unknown value {effect!r} (expected one of {sorted(known_effects)})"
            )
        align = (definition.get("layout") or {}).get("align")
        known_aligns = {"left", "center", "right"}
        if align is not None and align not in known_aligns:
            raise StyleValidationFailedError(
                f"layout.align: unknown value {align!r} (expected one of {sorted(known_aligns)})"
            )


class FakePreviewRenderer:
    """Implements the `PreviewRenderer` protocol in memory -- no ffmpeg, no
    whisper model, no filesystem rendering. Jobs stay `pending` until a test
    calls `force_status()` to move them along (mirrors
    `FakeJobQueue.force_status`), so tests can exercise the polling flow
    deterministically."""

    def __init__(self, *, style_provider: FakeStyleProvider | None = None) -> None:
        self._style_provider = style_provider
        self._jobs: dict[str, PreviewJob] = {}

    def submit_preview(self, video_path: Path, start_seconds: float, style: dict[str, Any]) -> PreviewJob:
        if self._style_provider is not None:
            self._style_provider._validate(dict(style, name=style.get("name") or "preview"))
        job = PreviewJob(id=uuid.uuid4().hex, status=PreviewStatus.PENDING)
        self._jobs[job.id] = job
        return job

    def get_preview(self, job_id: str) -> PreviewJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise PreviewNotFoundError(job_id)
        return job

    # Test helper only -- not part of the PreviewRenderer protocol.
    def force_status(self, job_id: str, status: PreviewStatus, **fields: Any) -> PreviewJob:
        job = self._jobs[job_id]
        updated = job.model_copy(update={"status": status, **fields})
        self._jobs[job_id] = updated
        return updated


# --- In-app updates (spec 11.4), glossary and desktop fakes ------------------
# Live in fakes_updates.py / fakes_providers.py (this module was over the
# 500-line limit); re-exported so tests keep importing them from here.
from .fakes_providers import FakeFilePicker, FakeGlossaryProvider, FakeRevealer  # noqa: E402, F401
from .fakes_updates import FakeUpdateApplier, FakeUpdateInfo, FakeUpdateState  # noqa: E402, F401
