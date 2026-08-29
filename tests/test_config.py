"""Tests for per-machine configuration and hardware detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ash_captions import config
from ash_captions.config import Settings, recommended_model


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ASH_CAPTIONS_ROOT", str(tmp_path))
    return tmp_path


def test_data_root_follows_env_override(root: Path) -> None:
    assert config.data_root() == root


def test_defaults_match_the_eighty_percent_path(root: Path) -> None:
    """Dropping a file in `in\\` with no options must do the common thing."""
    settings = Settings()
    assert settings.default_language == "en"
    assert settings.default_preset == "POP"
    assert settings.default_burn is False


def test_device_defaults_to_cpu(root: Path) -> None:
    """GPU is opt-in per machine; see spec 11.2."""
    assert Settings().device == "cpu"


def test_ensure_dirs_creates_the_working_tree(root: Path) -> None:
    settings = Settings()
    settings.ensure_dirs()
    assert settings.in_dir.is_dir()
    assert settings.out_dir.is_dir()
    assert settings.glossary_dir.is_dir()


def test_settings_round_trip(root: Path) -> None:
    original = Settings(model_size="large-v3", device="cuda", port=9001)
    original.save()
    loaded = Settings.load()
    assert loaded.model_size == "large-v3"
    assert loaded.device == "cuda"
    assert loaded.port == 9001
    assert isinstance(loaded.in_dir, Path)


def test_load_returns_defaults_when_absent(root: Path) -> None:
    assert Settings.load().model_size == "small"


@pytest.mark.parametrize("garbage", ["{not json", '"a string"', "[1, 2]", ""])
def test_corrupt_settings_file_never_stops_startup(root: Path, garbage: str) -> None:
    """An editor cannot debug JSON. A working default beats a dialog."""
    Settings.config_path().write_text(garbage, encoding="utf-8")
    assert Settings.load().model_size == "small"


def test_unknown_keys_are_ignored(root: Path) -> None:
    """Settings written by a newer version must not break an older one."""
    Settings.config_path().write_text(
        json.dumps({"model_size": "medium", "some_future_key": 42}), encoding="utf-8"
    )
    assert Settings.load().model_size == "medium"


def test_bundled_binary_wins_over_path(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """We ship our own LGPL ffmpeg rather than inheriting whatever is on PATH."""
    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "bin" / "ffmpeg.exe").write_text("stub", encoding="utf-8")
    monkeypatch.setattr(config, "app_root", lambda: bundle)

    found = config.find_binary("ffmpeg")
    assert found == bundle / "bin" / "ffmpeg.exe"


def test_find_binary_returns_none_when_missing(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "app_root", lambda: tmp_path)
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    assert config.find_binary("definitely-not-a-real-binary") is None


def test_has_nvidia_gpu_false_without_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    assert config.has_nvidia_gpu() is False


def test_has_nvidia_gpu_survives_a_broken_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-installed driver must report 'no GPU', not crash the app."""
    monkeypatch.setattr(config.shutil, "which", lambda _: "nvidia-smi")

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("driver is in a bad state")

    monkeypatch.setattr(config.subprocess, "run", explode)
    assert config.has_nvidia_gpu() is False


def test_recommended_model_by_device() -> None:
    assert recommended_model("cuda") == "large-v3"
    assert recommended_model("cpu") == "small"
