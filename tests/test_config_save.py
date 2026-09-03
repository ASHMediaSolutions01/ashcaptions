"""Settings.save() must be atomic: a truncated settings.json is read back
as "corrupt, use defaults", which silently resets every setting."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ash_captions.config import Settings


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ASH_CAPTIONS_ROOT", str(tmp_path))
    return tmp_path


def test_save_leaves_a_complete_file_and_no_temp(root: Path) -> None:
    settings = Settings()
    settings.save()

    path = Settings.config_path()
    assert path.is_file()
    assert not path.with_name(path.name + ".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["port"] == settings.port
    assert Settings.load().port == settings.port


def test_save_replaces_an_existing_file_in_place(root: Path) -> None:
    Settings().save()
    path = Settings.config_path()
    before = path.read_text(encoding="utf-8")
    Settings().save()
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in path.parent.iterdir() if p.name.startswith(path.name)) == [path.name]
