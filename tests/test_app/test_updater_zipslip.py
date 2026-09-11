"""The updater refuses an archive that would write outside its staging dir.

Reaching the extract already means the artifact matched the manifest's
sha256, so an archive that does this is a compromised manifest rather
than a corrupt download. That is precisely the moment the app is about to
mirror the extracted tree over its own install directory, so "the hash
matched" is not a reason to trust the archive's own idea of where its
files go.
"""
from __future__ import annotations

import zipfile

import pytest

from ash_captions.app.updater import UpdateApplyError, apply_update


def _zip_with(tmp_path, *names):
    path = tmp_path / "update.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return path


def _apply(artifact, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    return apply_update(
        artifact, install_dir=install, extract_to=tmp_path / "staging",
        spawn_helper=lambda argv: None, has_running_job=lambda: False,
    )


@pytest.mark.parametrize(
    "escaping",
    [
        "../escaped.txt",
        "AshCaptions/../../escaped.txt",
        # A Windows-style separator inside a member name. Zip names are
        # "/"-separated by spec, so a producer that writes this is either
        # broken or trying something.
        "..\\escaped.txt",
    ],
)
def test_a_member_that_climbs_out_of_staging_is_refused(tmp_path, escaping):
    artifact = _zip_with(tmp_path, "AshCaptions/AshCaptions.exe", escaping)
    with pytest.raises(UpdateApplyError) as caught:
        _apply(artifact, tmp_path)
    assert "outside" in str(caught.value)
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_an_ordinary_archive_still_extracts(tmp_path):
    """The guard must not reject the bundle build.py actually produces."""
    artifact = _zip_with(
        tmp_path, "AshCaptions/AshCaptions.exe", "AshCaptions/styles/CLEAN.json"
    )
    _apply(artifact, tmp_path)
    assert (tmp_path / "staging" / "AshCaptions" / "AshCaptions.exe").is_file()
