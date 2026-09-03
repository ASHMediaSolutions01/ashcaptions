"""Bridges web's `ClientGlossaryProvider` protocol onto the glossary files
in `settings.glossary_dir` (see `ash_captions.languages.glossary`). Consumes
that package's public interface only -- the same relationship
`styles_adapter.py` has to `ash_captions.styles`.

Writes are temp-file-plus-replace: the runner may read a client's file at
the moment an editor saves it, and must see either the old text or the
new, never a truncated middle.
"""
from __future__ import annotations

import os
from pathlib import Path

from ash_captions.languages import (
    SHARED_GLOSSARY_FILENAME,
    client_glossary_path,
    client_slug,
    validate_glossary_text,
)

from .interfaces import GlossaryValidationFailedError


class ClientGlossaryFiles:
    """Implements `ClientGlossaryProvider` over `<glossary_dir>/<slug>.txt`."""

    def __init__(self, glossary_dir: Path) -> None:
        self._dir = Path(glossary_dir)

    @property
    def glossary_dir(self) -> Path:
        return self._dir

    def list_clients(self) -> list[str]:
        try:
            names = sorted(p.stem for p in self._dir.glob("*.txt") if p.is_file())
        except OSError:
            return []
        return [name for name in names if f"{name}.txt" != SHARED_GLOSSARY_FILENAME]

    def slug_for(self, client: str) -> str:
        return client_slug(client)

    def read_glossary(self, client: str) -> str:
        path = self._path(client)
        try:
            return path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return ""
        except UnicodeDecodeError:
            # The runner would already treat this file as empty; show the
            # editor the same thing rather than a 500.
            return ""

    def write_glossary(self, client: str, text: str) -> None:
        problems = validate_glossary_text(text)
        if problems:
            raise GlossaryValidationFailedError(problems)
        path = self._path(client)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = text.replace("\r\n", "\n")
        if normalized and not normalized.endswith("\n"):
            normalized += "\n"
        partial = path.with_name(path.name + ".part")
        partial.write_text(normalized, encoding="utf-8")
        os.replace(partial, path)

    def _path(self, client: str) -> Path:
        # `client_glossary_path` re-checks the slug is a plain file stem;
        # the route sanitized the name already, so a ValueError here is a
        # programming error rather than user input -- let it surface.
        return client_glossary_path(self._dir, client)
