"""The four web <-> pipeline conversions behind ``QueueAdapter`` (see
``adapter.py``'s module docstring for the mismatches: id type, progress
scale, options shape, filename). Split out so the adapter stays about
queueing and pushing; nothing here touches the store."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ash_captions.pipeline.db import Job as PipelineJob
from ash_captions.pipeline.db import JobOptions as PipelineJobOptions
from ash_captions.web.models import Job as WebJob
from ash_captions.web.models import JobOptions as WebJobOptions
from ash_captions.web.models import JobStatus as WebJobStatus

# Extra web Job fields, set only when the model declares them (pydantic
# would silently drop unknown kwargs and hide a wiring gap).
_OPTIONAL_WEB_FIELDS = ("stage", "stage_started_at", "started_at", "input_path", "output_dir")


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
        caption_x=options.caption_x,
        caption_y=options.caption_y,
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
        caption_x=options.caption_x,
        caption_y=options.caption_y,
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
