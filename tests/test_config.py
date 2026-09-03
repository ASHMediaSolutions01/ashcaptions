"""Tests for per-machine configuration and hardware detection, including
the per-field coercion that keeps a hand-edited settings.json from either
crashing startup or silently disabling a feature."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ash_captions import config
from ash_captions.config import MODEL_SIZES, Settings, recommended_model


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ASH_CAPTIONS_ROOT", str(tmp_path))
    return tmp_path


def _write(root: Path, payload: dict) -> None:
    Settings.config_path().write_text(json.dumps(payload), encoding="utf-8")


def test_data_root_follows_env_override(root: Path) -> None:
    assert config.data_root() == root


def test_defaults_match_the_eighty_percent_path(root: Path) -> None:
    settings = Settings()
    assert settings.default_language == "en"
    assert settings.default_preset == "POP"
    assert settings.default_burn is False


def test_device_defaults_to_cpu(root: Path) -> None:
    assert Settings().device == "cpu"


def test_new_engine_and_housekeeping_defaults(root: Path) -> None:
    settings = Settings()
    assert settings.cpu_threads is None
    assert settings.condition_on_previous_text is False
    assert settings.hallucination_silence_threshold == 2.0
    assert settings.min_free_disk_gb == 5.0
    assert settings.upload_dir == root / "web_uploads"
    assert settings.tmp_dir == root / "tmp"


def test_ensure_dirs_creates_the_working_tree(root: Path) -> None:
    settings = Settings()
    settings.ensure_dirs()
    for path in (settings.in_dir, settings.out_dir, settings.glossary_dir, settings.upload_dir, settings.tmp_dir):
        assert path.is_dir()


def test_settings_round_trip(root: Path) -> None:
    original = Settings(model_size="large-v3", device="cuda", port=9001, cpu_threads=4)
    original.save()
    loaded = Settings.load()
    assert loaded.model_size == "large-v3"
    assert loaded.device == "cuda"
    assert loaded.port == 9001
    assert loaded.cpu_threads == 4
    assert isinstance(loaded.in_dir, Path)


def test_load_returns_defaults_when_absent(root: Path) -> None:
    assert Settings.load().model_size == "small"


@pytest.mark.parametrize("garbage", ["{not json", '"a string"', "[1, 2]", ""])
def test_corrupt_settings_file_never_stops_startup(root: Path, garbage: str) -> None:
    Settings.config_path().write_text(garbage, encoding="utf-8")
    assert Settings.load().model_size == "small"


def test_a_utf8_bom_does_not_reset_every_setting(root: Path) -> None:
    """Notepad and PowerShell's Set-Content write a BOM; found for real when
    a hand-written settings.json silently came up with port 8756 and no burn."""
    Settings.config_path().write_bytes(b"\xef\xbb\xbf" + json.dumps({"port": 8791, "default_burn": True}).encode())
    loaded = Settings.load()
    assert loaded.port == 8791
    assert loaded.default_burn is True


def test_unknown_keys_are_ignored(root: Path) -> None:
    _write(root, {"model_size": "medium", "some_future_key": 42})
    assert Settings.load().model_size == "medium"


class TestPerFieldCoercion:
    """One bad field falls back to its own default with a warning; every
    other field in the file is still honoured."""

    def test_quoted_port_is_coerced(self, root: Path) -> None:
        _write(root, {"port": "8756"})
        assert Settings.load().port == 8756

    def test_quoted_retention_days_and_gap_are_coerced(self, root: Path) -> None:
        _write(root, {"retention_days": "30", "silence_gap_seconds": "1.5"})
        loaded = Settings.load()
        assert loaded.retention_days == 30 and isinstance(loaded.retention_days, int)
        assert loaded.silence_gap_seconds == 1.5 and isinstance(loaded.silence_gap_seconds, float)

    @pytest.mark.parametrize(
        "field, bad, default",
        [
            ("port", 80, config.DEFAULT_PORT),
            ("port", 70000, config.DEFAULT_PORT),
            ("port", "eighty", config.DEFAULT_PORT),
            ("retention_days", -1, 30),
            ("retention_days", "thirty", 30),
            ("silence_gap_seconds", 0.01, 1.5),
            ("silence_gap_seconds", 11, 1.5),
            ("punch_zoom", 0.5, 1.12),
            ("punch_zoom", 3.0, 1.12),
            ("punch_duration_seconds", 0, 1.2),
            ("punch_min_spacing_seconds", 601, 5.0),
            ("model_size", "enormous", "small"),
            ("device", "gpu", "cpu"),
            ("punch_mode", "always", "off"),
            ("cpu_threads", -3, None),
            ("cpu_threads", "lots", None),
            ("hallucination_silence_threshold", -1, 2.0),
            ("min_free_disk_gb", "plenty", 5.0),
            ("default_burn", "sometimes", False),
            ("in_dir", "", None),
            ("default_language", "", "en"),
        ],
    )
    def test_bad_values_fall_back_per_field_with_a_warning(self, root: Path, field: str, bad, default) -> None:
        _write(root, {field: bad, "default_preset": "CLEAN"})
        warnings: list[str] = []

        loaded = Settings.load(on_warning=warnings.append)

        expected = getattr(Settings(), field) if default is None and field == "in_dir" else default
        assert getattr(loaded, field) == expected
        assert loaded.default_preset == "CLEAN"  # the rest of the file still applies
        assert len(warnings) == 1 and field in warnings[0]

    def test_valid_choices_are_case_normalised(self, root: Path) -> None:
        _write(root, {"model_size": "Medium", "device": "CUDA", "punch_mode": "Sentence"})
        loaded = Settings.load()
        assert (loaded.model_size, loaded.device, loaded.punch_mode) == ("medium", "cuda", "sentence")

    def test_auto_device_is_accepted(self, root: Path) -> None:
        _write(root, {"device": "auto"})
        assert Settings.load().device == "auto"

    def test_model_sizes_are_the_literal(self) -> None:
        assert "small" in MODEL_SIZES and "large-v3" in MODEL_SIZES

    def test_bools_accept_strings_and_ints(self, root: Path) -> None:
        _write(root, {"default_burn": "true", "default_translate": 1, "condition_on_previous_text": "False"})
        loaded = Settings.load()
        assert loaded.default_burn is True
        assert loaded.default_translate is True
        assert loaded.condition_on_previous_text is False

    def test_null_clears_optional_fields(self, root: Path) -> None:
        _write(root, {"hallucination_silence_threshold": None, "cpu_threads": None, "default_dialect": None})
        loaded = Settings.load()
        assert loaded.hallucination_silence_threshold is None
        assert loaded.cpu_threads is None

    def test_keywords_accept_a_list_or_a_comma_string(self, root: Path) -> None:
        _write(root, {"punch_keywords": ["free", " money "]})
        assert Settings.load().punch_keywords == ("free", "money")
        _write(root, {"punch_keywords": "free, money"})
        assert Settings.load().punch_keywords == ("free", "money")

    def test_path_fields_become_paths(self, root: Path) -> None:
        _write(root, {"upload_dir": "D:/uploads", "tmp_dir": "D:/scratch"})
        loaded = Settings.load()
        assert loaded.upload_dir == Path("D:/uploads")
        assert loaded.tmp_dir == Path("D:/scratch")

    def test_warnings_default_to_the_module_logger(self, root: Path, caplog: pytest.LogCaptureFixture) -> None:
        _write(root, {"port": "nope"})
        with caplog.at_level("WARNING", logger="ash_captions.config"):
            Settings.load()
        assert any("port" in r.getMessage() for r in caplog.records)


def test_bundled_binary_wins_over_path(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "bin" / "ffmpeg.exe").write_text("stub", encoding="utf-8")
    monkeypatch.setattr(config, "app_root", lambda: bundle)
    assert config.find_binary("ffmpeg") == bundle / "bin" / "ffmpeg.exe"


def test_find_binary_returns_none_when_missing(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "app_root", lambda: tmp_path)
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    assert config.find_binary("definitely-not-a-real-binary") is None


def test_has_nvidia_gpu_false_without_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    assert config.has_nvidia_gpu() is False


def test_has_nvidia_gpu_survives_a_broken_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.shutil, "which", lambda _: "nvidia-smi")

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("driver is in a bad state")

    monkeypatch.setattr(config.subprocess, "run", explode)
    assert config.has_nvidia_gpu() is False


def test_recommended_model_by_device() -> None:
    assert recommended_model("cuda") == "large-v3"
    assert recommended_model("cpu") == "small"


def test_silence_gap_is_tunable_without_a_release(root: Path) -> None:
    assert Settings().silence_gap_seconds == 1.5
    Settings(silence_gap_seconds=0.9).save()
    assert Settings.load().silence_gap_seconds == 0.9
