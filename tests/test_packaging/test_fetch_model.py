"""Tests for scripts/fetch_model.py.

`download_model()` is the only network-touching function (it calls
huggingface_hub.snapshot_download) and is never called here.
"""

from __future__ import annotations

import fetch_model
import pytest


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


def test_write_model_info(tmp_path):
    info_path = fetch_model.write_model_info(
        tmp_path,
        model_size="small",
        repo_id="Systran/faster-whisper-small",
        revision=None,
        size_bytes=507510000,
    )
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
