"""After an update, the staged bundle and downloaded zips are removed."""
from pathlib import Path

from ash_captions.app.updater import clean_update_leftovers


def test_removes_staging_zips_helper_and_parts(tmp_path: Path):
    (tmp_path / "staged_update" / "AshCaptions").mkdir(parents=True)
    (tmp_path / "staged_update" / "AshCaptions" / "AshCaptions.exe").write_bytes(b"x")
    (tmp_path / "AshCaptions-0.4.0-win64.zip").write_bytes(b"z")
    (tmp_path / "AshCaptions-0.4.1-win64.zip.part").write_bytes(b"z")
    (tmp_path / "apply_update.ps1").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")

    assert clean_update_leftovers(tmp_path) == 4
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]


def test_missing_dir_is_fine(tmp_path: Path):
    assert clean_update_leftovers(tmp_path / "nope") == 0
