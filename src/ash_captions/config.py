"""Application configuration and hardware detection.

Everything that varies per machine lives here. The tool ships to six editors'
PCs with different CPUs, GPUs and drivers, so nothing below may assume a
particular machine — see the spec, section 11.2, on why GPU is opt-in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Device = Literal["cpu", "cuda"]
ModelSize = Literal["tiny", "base", "small", "medium", "large-v3"]

APP_NAME = "AshCaptions"
DEFAULT_ROOT = Path("C:/AshCaptions")

# Bound the port scan so a stuck instance can't make us wander the port space.
DEFAULT_PORT = 8756
MAX_PORT_PROBES = 20


def app_root() -> Path:
    """Where the installed application lives.

    Under PyInstaller onedir the bundle sits beside the executable; in a source
    checkout it is the repository root. Both need to resolve `bin/ffmpeg.exe`.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """Where jobs, outputs and the database live. Overridable for testing."""
    override = os.environ.get("ASH_CAPTIONS_ROOT")
    return Path(override) if override else DEFAULT_ROOT


def find_binary(name: str) -> Path | None:
    """Locate a bundled binary, falling back to PATH.

    Bundled wins deliberately: the spec requires ffmpeg to ship in `bin/` so a
    machine without ffmpeg installed still works, and so we control the build
    (LGPL static) rather than inheriting whatever is on PATH.
    """
    exe = name if name.endswith(".exe") else f"{name}.exe"
    bundled = app_root() / "bin" / exe
    if bundled.is_file():
        return bundled
    found = shutil.which(name)
    return Path(found) if found else None


def has_nvidia_gpu() -> bool:
    """True when an NVIDIA GPU is present.

    `nvidia-smi` ships with the display driver itself, not only the CUDA
    toolkit, so its presence is a reliable signal on a plain gaming/editing
    machine. This says nothing about whether the CUDA/cuDNN versions match what
    ctranslate2 needs — see `Settings.device`, which is why GPU stays opt-in.
    """
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


@dataclass
class Settings:
    """Per-machine settings, persisted as JSON beside the database."""

    # Paths
    in_dir: Path = field(default_factory=lambda: data_root() / "in")
    out_dir: Path = field(default_factory=lambda: data_root() / "out")
    db_path: Path = field(default_factory=lambda: data_root() / "jobs.db")
    log_path: Path = field(default_factory=lambda: data_root() / "ash-captions.log")
    glossary_dir: Path = field(default_factory=lambda: data_root() / "glossaries")

    # Engine
    model_size: ModelSize = "small"
    # Defaults to CPU on every machine. GPU is enabled per-machine after
    # checking the driver against the ctranslate2 CUDA/cuDNN matrix; a CPU
    # install that works on all six beats a GPU install that breaks on three.
    device: Device = "cpu"

    # Job defaults, matching the 80% path: English, POP, no burn.
    default_language: str = "en"
    default_dialect: str | None = None
    default_preset: str = "POP"
    default_burn: bool = False
    default_translate: bool = False

    # Editorial timing. A caption card is never allowed to span a gap longer
    # than this, and a lone word marooned by gaps this size on both sides is
    # dropped as a voice-activity survivor rather than shown as a phantom
    # caption. 1.5s is a considered default, not a measured one -- it is here
    # rather than hardcoded in the engine so it can be tuned against real
    # client audio without a release. Too low gives choppy cards; too high
    # lets one card bridge a pause the viewer can hear.
    silence_gap_seconds: float = 1.5

    # Housekeeping
    retention_days: int = 30
    port: int = DEFAULT_PORT

    @property
    def model_cache_dir(self) -> Path:
        """Pre-seeded model cache shipped with the installer.

        Six machines each pulling multiple GB from HuggingFace over the office
        connection is not acceptable, so the installer ships the model and we
        point faster-whisper here rather than at the default user cache.
        """
        bundled = app_root() / "models"
        return bundled if bundled.is_dir() else data_root() / "models"

    def ensure_dirs(self) -> None:
        for path in (self.in_dir, self.out_dir, self.glossary_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence ----------------------------------------------------

    @classmethod
    def config_path(cls) -> Path:
        return data_root() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        """Load settings, falling back to defaults on anything unreadable.

        A corrupt settings file must never stop the tool starting — an editor
        cannot debug JSON, and a working default is always better than a dialog.
        """
        path = cls.config_path()
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()

        known = {f for f in cls.__dataclass_fields__}
        path_fields = {"in_dir", "out_dir", "db_path", "log_path", "glossary_dir"}
        kwargs: dict[str, object] = {}
        for key, value in raw.items():
            if key not in known:
                continue
            kwargs[key] = Path(value) if key in path_fields else value
        try:
            return cls(**kwargs)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return cls()

    def save(self) -> None:
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self).items()
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def recommended_model(device: Device) -> ModelSize:
    """Pick a model size the machine can actually run at a sane speed."""
    if device == "cuda":
        return "large-v3"
    # CPU: `small` is the honest default for EN/ES/PT short-form work. `medium`
    # is roughly 3x the weight for a modest accuracy gain and makes a laptop
    # crawl, so it stays a deliberate per-machine choice rather than a default.
    return "small"
