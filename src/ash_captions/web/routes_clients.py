"""Client routes: who the studio's clients are, and each one's glossary.

* `GET /api/clients` -- names for the control page's client picker: every
  client with a glossary file (by slug) plus the distinct clients on recent
  jobs (`queue.known_clients()`, when the queue has it), most recently used
  first, deduplicated case-insensitively with the job's spelling preferred
  over the file slug.
* `GET /api/clients/{client}/glossary` -- the file's text ("" if absent).
* `PUT /api/clients/{client}/glossary {"text"}` -- validate every line with
  the real glossary parser's rules and replace the file atomically; line
  problems come back as a 400 so nothing is silently dropped.

`{client}` is the display name or the slug -- both map to the same file.
It is sanitized exactly like the job routes' `client` field, so a name
that could escape the glossary folder is a 400 before it becomes a path.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .interfaces import ClientGlossaryProvider, GlossaryValidationFailedError, JobQueue
from .models import ClientGlossary, GlossaryTextRequest
from .validation import validate_client_name


def merge_client_names(from_files: list[str], from_jobs: list[str]) -> list[str]:
    """Jobs' spellings first (most recent first), then file slugs not already
    covered; one entry per lower-cased name."""
    seen: dict[str, str] = {}
    for name in list(from_jobs) + list(from_files):
        seen.setdefault(name.lower(), name)
    return list(seen.values())


def build_clients_router(
    get_queue: Callable[[Request], JobQueue],
    get_glossary_provider: Callable[[Request], ClientGlossaryProvider],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/clients", response_model=list[str])
    async def list_clients(
        queue: JobQueue = Depends(get_queue),
        glossaries: ClientGlossaryProvider = Depends(get_glossary_provider),
    ) -> list[str]:
        known = getattr(queue, "known_clients", None)
        from_jobs = list(known()) if callable(known) else []
        from_files = await run_in_threadpool(glossaries.list_clients)
        return merge_client_names(from_files, from_jobs)

    @router.get("/api/clients/{client}/glossary", response_model=ClientGlossary)
    async def get_glossary(
        client: str,
        glossaries: ClientGlossaryProvider = Depends(get_glossary_provider),
    ) -> ClientGlossary:
        name = _require_client(client)
        text = await run_in_threadpool(glossaries.read_glossary, name)
        return ClientGlossary(client=name, slug=glossaries.slug_for(name), text=text)

    @router.put("/api/clients/{client}/glossary", response_model=ClientGlossary)
    async def put_glossary(
        client: str,
        body: GlossaryTextRequest,
        glossaries: ClientGlossaryProvider = Depends(get_glossary_provider),
    ) -> ClientGlossary:
        name = _require_client(client)
        try:
            await run_in_threadpool(glossaries.write_glossary, name, body.text)
        except GlossaryValidationFailedError as exc:
            raise HTTPException(status_code=400, detail=_problems_detail(exc.problems))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Couldn't write the glossary file: {exc.strerror or exc}.")
        text = await run_in_threadpool(glossaries.read_glossary, name)
        return ClientGlossary(client=name, slug=glossaries.slug_for(name), text=text)

    return router


def _require_client(raw: str) -> str:
    name = validate_client_name(raw)
    if name is None:
        raise HTTPException(status_code=400, detail="A client name is required.")
    return name


def _problems_detail(problems: list[str]) -> str:
    shown = problems[:10]
    more = f" (and {len(problems) - len(shown)} more)" if len(problems) > len(shown) else ""
    return "Glossary not saved -- fix these lines first: " + "; ".join(shown) + more
