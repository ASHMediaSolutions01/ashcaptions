"""Fakes for the glossary and desktop protocols (`ClientGlossaryProvider`,
`FilePicker`, `PathRevealer`). Moved out of fakes.py for size; re-exported
from there so tests keep importing them from `.fakes`."""

from __future__ import annotations

from pathlib import Path

from ash_captions.web.interfaces import GlossaryValidationFailedError, PickerBusyError


# --- Per-client glossaries ---------------------------------------------------


class FakeGlossaryProvider:
    """Implements `ClientGlossaryProvider` in memory, keyed by slug. Its
    validation mirrors `languages.validate_glossary_text`'s one rule the
    routes care about -- a `=>` line needs both sides -- so the 400 path
    is exercised without the real package."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []

    def slug_for(self, client: str) -> str:
        return "-".join(client.strip().lower().split())

    def list_clients(self) -> list[str]:
        return sorted(self.files)

    def read_glossary(self, client: str) -> str:
        return self.files.get(self.slug_for(client), "")

    def write_glossary(self, client: str, text: str) -> None:
        problems = []
        for number, line in enumerate(text.splitlines(), start=1):
            if "=>" in line:
                left, _, right = line.partition("=>")
                if not left.strip() or not right.strip():
                    problems.append(f"line {number}: incomplete 'wrong => right' pair")
        if problems:
            raise GlossaryValidationFailedError(problems)
        self.files[self.slug_for(client)] = text
        self.writes.append((client, text))


# --- The editor's desktop ------------------------------------------------------


class FakeFilePicker:
    """Implements `FilePicker`: answers whatever `result` holds (a path or
    None for "cancelled"); `busy` makes it behave like a dialog that is
    already open."""

    def __init__(self, result: str | None = None, *, busy: bool = False) -> None:
        self.result = result
        self.busy = busy
        self.calls = 0

    def pick_video(self) -> str | None:
        self.calls += 1
        if self.busy:
            raise PickerBusyError("already open")
        return self.result


class FakeRevealer:
    """Implements `PathRevealer`: records what it was asked to show."""

    def __init__(self) -> None:
        self.revealed: list[Path] = []

    def reveal(self, path: Path) -> None:
        if not Path(path).exists():
            raise FileNotFoundError(str(path))
        self.revealed.append(Path(path))
