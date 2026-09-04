"""The bundle ships the collected licence texts and refuses to build without them."""
from pathlib import Path

import pytest

import build


def test_validate_licenses_dir_needs_the_index(tmp_path):
    with pytest.raises(build.BuildError, match="collect_licenses"):
        build.validate_licenses_dir(tmp_path)
    (tmp_path / build.LICENSES_INDEX).write_text("x", encoding="utf-8")
    build.validate_licenses_dir(tmp_path)


def test_licenses_dir_is_added_as_data(tmp_path):
    entry = tmp_path / "m.py"
    entry.write_text("", encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    lic = tmp_path / "licenses"
    lic.mkdir()
    args = build.build_pyinstaller_args(
        entry_script=entry, dist_dir=tmp_path / "d", work_dir=tmp_path / "w", spec_dir=tmp_path / "s",
        static_dir=static, licenses_dir=lic,
    )
    assert f"{lic.resolve()};licenses" in args
