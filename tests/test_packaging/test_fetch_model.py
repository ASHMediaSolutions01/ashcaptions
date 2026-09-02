"""Tests for scripts/fetch_model.py.

`download_model()` is the only network-touching function (it goes through
faster_whisper.utils.download_model -> huggingface_hub) and is never called
here. The layout it must produce IS exercised: a bundle laid out exactly
as build.py's `--add-data <cache>;models` produces is resolved offline by
the very same faster-whisper lookup the runtime performs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import build
import fetch_model
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_model_sizes_match_config_module():
    """scripts/fetch_model.py's --model-size choices must stay in lockstep
    with config.py's ModelSize, or a pre-seeded model could be built under a
    name Settings.model_size never sets."""
    from typing import get_args

    import ash_captions.config as config

    assert set(fetch_model.MODEL_SIZES) == set(get_args(config.ModelSize))


@pytest.mark.parametrize(
    "size, expected_repo",
    [
        ("tiny", "Systran/faster-whisper-tiny"),
        ("small", "Systran/faster-whisper-small"),
        ("large-v3", "Systran/faster-whisper-large-v3"),
    ],
)
def test_resolve_repo_id(size, expected_repo):
    assert fetch_model.resolve_repo_id(size) == expected_repo


def test_resolve_repo_id_rejects_unknown_size():
    with pytest.raises(fetch_model.FetchModelError):
        fetch_model.resolve_repo_id("xl-turbo")


def test_repo_cache_dir_is_the_hf_cache_folder_name(tmp_path):
    assert fetch_model.repo_cache_dir(tmp_path, "small") == tmp_path / "models--Systran--faster-whisper-small"


def test_dir_size_bytes(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 250)

    assert fetch_model.dir_size_bytes(tmp_path) == 350


def test_dir_size_bytes_empty_dir(tmp_path):
    assert fetch_model.dir_size_bytes(tmp_path) == 0


@pytest.mark.parametrize(
    "num_bytes, expected_prefix",
    [
        (500, "500 B"),
        (2048, "2.0 KB"),
        (500 * 1024 * 1024, "500.0 MB"),
        (3 * 1024 * 1024 * 1024, "3.0 GB"),
    ],
)
def test_human_readable_size(num_bytes, expected_prefix):
    assert fetch_model.human_readable_size(num_bytes) == expected_prefix


def test_write_model_info_is_per_size_so_sizes_can_share_a_cache_root(tmp_path):
    info_path = fetch_model.write_model_info(
        tmp_path,
        model_size="small",
        repo_id="Systran/faster-whisper-small",
        revision=None,
        size_bytes=507510000,
    )
    assert info_path.name == "model-info-small.txt"
    text = info_path.read_text(encoding="utf-8")
    assert "model_size: small" in text
    assert "Systran/faster-whisper-small" in text
    assert "(default branch)" in text
    assert "507510000 bytes" in text


def test_parse_args_requires_model_size():
    with pytest.raises(SystemExit):
        fetch_model.parse_args([])


def test_parse_args_default_dest_is_none():
    args = fetch_model.parse_args(["--model-size", "medium"])
    assert args.model_size == "medium"
    assert args.dest is None


def test_default_cache_root_is_what_install_docs_pass_to_build():
    assert fetch_model.DEFAULT_CACHE_ROOT == fetch_model.REPO_ROOT / "build" / "models"


# -- HF cache layout ---------------------------------------------------------


def _fake_hf_cache(root: Path, size: str = "tiny", *, link_blobs: bool = False) -> Path:
    repo_dir = fetch_model.repo_cache_dir(root, size)
    snapshot = repo_dir / "snapshots" / FAKE_SHA
    snapshot.mkdir(parents=True)
    (repo_dir / "refs").mkdir()
    (repo_dir / "refs" / "main").write_text(FAKE_SHA, encoding="utf-8")
    blobs = repo_dir / "blobs"
    blobs.mkdir()
    for i, name in enumerate(("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")):
        payload = f"fake {name}".encode()
        if link_blobs:
            blob = blobs / f"blob{i}"
            blob.write_bytes(payload)
            (snapshot / name).symlink_to(blob)
        else:
            (snapshot / name).write_bytes(payload)
    return snapshot


def test_find_snapshot_dirs(tmp_path):
    assert fetch_model.find_snapshot_dirs(tmp_path) == []
    snapshot = _fake_hf_cache(tmp_path)
    assert fetch_model.find_snapshot_dirs(tmp_path) == [snapshot]


def test_materialize_snapshots_replaces_links_and_drops_blobs(tmp_path):
    try:
        snapshot = _fake_hf_cache(tmp_path, link_blobs=True)
    except OSError:
        pytest.skip("symlinks not permitted for this user")
    (tmp_path / ".locks").mkdir()

    replaced = fetch_model.materialize_snapshots(tmp_path)

    assert replaced == 4
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        path = snapshot / name
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == f"fake {name}".encode()
    assert not (snapshot.parent.parent / "blobs").exists()
    assert not (tmp_path / ".locks").exists()
    assert fetch_model.materialize_snapshots(tmp_path) == 0  # idempotent


def test_materialize_snapshots_is_a_noop_on_real_files(tmp_path):
    _fake_hf_cache(tmp_path)
    assert fetch_model.materialize_snapshots(tmp_path) == 0


def _build_frozen_bundle(tmp_path: Path, *, flat: bool) -> Path:
    """Lay tmp_path out as build.py's `--add-data <cache>;models` produces
    inside the onedir bundle (exe at the root, models/ beside it)."""
    bundle_dir = tmp_path / build.APP_NAME
    fake_exe = bundle_dir / "AshCaptions.exe"
    bundle_dir.mkdir(parents=True)
    fake_exe.write_bytes(b"not a real PyInstaller exe -- only .parent is read")
    models = bundle_dir / "models"
    if flat:
        # The layout the old fetch_model.py produced: invisible to faster-whisper.
        (models / "tiny").mkdir(parents=True)
        for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
            (models / "tiny" / name).write_bytes(b"x")
    else:
        _fake_hf_cache(models)
    return fake_exe


_RESOLVE_SCRIPT = """
import os, sys
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, {src!r})
sys.frozen = True
sys.executable = {exe!r}
from ash_captions.config import Settings
from faster_whisper.utils import download_model
cache_dir = Settings().model_cache_dir
print("CACHE_DIR", cache_dir)
try:
    print("RESOLVED", download_model("tiny", cache_dir=str(cache_dir), local_files_only=True))
except Exception as exc:  # noqa: BLE001
    print("NOT_FOUND", type(exc).__name__, str(exc)[:200])
"""


def _run_frozen_resolve(fake_exe: Path) -> subprocess.CompletedProcess:
    pytest.importorskip("faster_whisper")
    return subprocess.run(
        [sys.executable, "-c", _RESOLVE_SCRIPT.format(src=str(SRC_DIR), exe=str(fake_exe))],
        capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT),
    )


def test_bundled_hf_cache_resolves_offline_when_frozen(tmp_path):
    """The proof: a frozen app's Settings.model_cache_dir is the bundled
    models/ directory, and faster-whisper's own lookup finds the model
    there with the network forbidden -- so no machine re-downloads."""
    fake_exe = _build_frozen_bundle(tmp_path, flat=False)
    proc = _run_frozen_resolve(fake_exe)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert f"CACHE_DIR {fake_exe.parent / 'models'}" in proc.stdout
    resolved = next(line for line in proc.stdout.splitlines() if line.startswith("RESOLVED "))
    assert Path(resolved[len("RESOLVED "):]).resolve() == (
        fake_exe.parent / "models" / "models--Systran--faster-whisper-tiny" / "snapshots" / FAKE_SHA
    ).resolve()


def test_flat_layout_is_not_found_offline_when_frozen(tmp_path):
    """Negative control -- the bug as shipped: models/tiny/model.bin is not
    a cache faster-whisper can see, so it would go to the network."""
    fake_exe = _build_frozen_bundle(tmp_path, flat=True)
    proc = _run_frozen_resolve(fake_exe)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "NOT_FOUND" in proc.stdout
