"""Application configuration and hardware detection.

Everything that varies per machine lives here. The tool ships to six editors'
PCs with different CPUs, GPUs and drivers, so nothing below may assume a
particular machine — see the spec, section 11.2, on why GPU is opt-in.

``settings.json`` is hand-edited by whoever installs the tool, so every
field is coerced and range-checked on load (``Settings.load``): a quoted
number, a typo'd model name, or a port outside the valid range falls back
to that one field's default with a logged warning, instead of either
crashing at startup (``"port": "8756"`` used to be a TypeError) or silently
disabling a feature (``"retention_days": "30"`` used to turn retention off).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Literal, get_args

log = logging.getLogger("ash_captions.config")

Device = Literal["cpu", "cuda", "auto"]
# The one list of model names this tool runs. scripts/fetch_model.py's
# --model-size choices derive from it too, so a pre-seeded model can never
# be built under a name Settings.model_size refuses.
ModelSize = Literal["tiny", "base", "small", "medium", "large-v3"]

MODEL_SIZES: tuple[str, ...] = get_args(ModelSize)
DEVICES: tuple[str, ...] = get_args(Device)
PUNCH_MODES: tuple[str, ...] = ("off", "sentence", "keyword", "both")

APP_NAME = "AshCaptions"
DEFAULT_ROOT = Path("C:/AshCaptions")

# Bound the port scan so a stuck instance can't make us wander the port space.
DEFAULT_PORT = 8756
MAX_PORT_PROBES = 20

PATH_FIELDS = frozenset(
    {"in_dir", "out_dir", "db_path", "log_path", "glossary_dir", "upload_dir", "tmp_dir"}
)


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
    (BtbN GPL static) rather than inheriting whatever is on PATH.
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
    # Where the control page's upload route copies a browser-submitted
    # file before queueing it. Treated like ``in_dir`` for post-success
    # deletion -- the copy is ours, never an editor's source file.
    upload_dir: Path = field(default_factory=lambda: data_root() / "web_uploads")
    # Per-job scratch (the extracted WAV). Under the data root rather than
    # %TEMP% so a killed job's leftovers are ours to sweep at next startup.
    tmp_dir: Path = field(default_factory=lambda: data_root() / "tmp")

    # Engine
    model_size: ModelSize = "small"
    # Defaults to CPU on every machine. GPU is enabled per-machine after
    # checking the driver against the ctranslate2 CUDA/cuDNN matrix; a CPU
    # install that works on all six beats a GPU install that breaks on three.
    device: Device = "cpu"
    # None lets ctranslate2 pick; a count pins it (an editor may want to
    # keep cores free for Premiere while an hour-long job runs).
    cpu_threads: int | None = None
    # Off: on a 60-90 minute file, conditioning on the previous window is
    # how a single hallucinated phrase repeats for the rest of the video.
    condition_on_previous_text: bool = False
    # Seconds of silence after which a "phrase" is treated as hallucinated
    # and dropped. None disables the check.
    hallucination_silence_threshold: float | None = 2.0

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

    # Punch-in: zoom the footage on chosen words when burning in. Off by
    # default -- it changes how a client's video is framed, so it is a
    # deliberate choice rather than something that happens to footage
    # silently. "sentence" is the sane setting once enabled; see
    # engine/punch.py for why the trigger is predictable rather than clever.
    punch_mode: str = "off"  # off | sentence | keyword | both
    punch_zoom: float = 1.12
    punch_duration_seconds: float = 1.2
    # Punching every sentence in fast dialogue is unwatchable, so this is
    # the minimum gap between two punches.
    punch_min_spacing_seconds: float = 5.0
    punch_keywords: tuple[str, ...] = ()

    # Housekeeping
    retention_days: int = 30
    port: int = DEFAULT_PORT
    # A burn-in is refused up front when the output drive has less than
    # max(this, 1.2 x input size) free -- an ffmpeg that dies at 97% after
    # an hour is far worse than a plain "not enough space" at the start.
    min_free_disk_gb: float = 5.0

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
        for path in (self.in_dir, self.out_dir, self.glossary_dir, self.upload_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence ----------------------------------------------------

    @classmethod
    def config_path(cls) -> Path:
        return data_root() / "settings.json"

    @classmethod
    def load(cls, *, on_warning: Callable[[str], None] | None = None) -> "Settings":
        """Load settings, falling back to defaults on anything unreadable.

        A corrupt settings file must never stop the tool starting — an editor
        cannot debug JSON, and a working default is always better than a dialog.
        Individual bad fields fall back one at a time (see module docstring);
        each is reported through ``on_warning`` (default: the module logger --
        pass a collector when logging isn't configured yet, as ``main()`` does).
        """
        warn = on_warning or log.warning
        path = cls.config_path()
        if not path.is_file():
            return cls()
        try:
            # utf-8-sig: Notepad (and PowerShell's Set-Content) write a BOM,
            # which plain utf-8 json.loads rejects -- and a rejected file
            # used to mean every setting silently reverted to its default.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            warn(f"settings.json is unreadable ({exc}); using defaults for everything.")
            return cls()
        if not isinstance(raw, dict):
            warn("settings.json is not a JSON object; using defaults for everything.")
            return cls()
        return cls.from_dict(raw, on_warning=warn)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, on_warning: Callable[[str], None] | None = None) -> "Settings":
        """Build settings from a parsed JSON object, coercing and range-
        checking every known field and ignoring unknown ones (a newer
        version's keys must never break an older one)."""
        warn = on_warning or log.warning
        defaults = cls()
        kwargs: dict[str, Any] = {}
        for spec in fields(cls):
            if spec.name not in raw:
                continue
            value = raw[spec.name]
            validator = _VALIDATORS.get(spec.name)
            try:
                kwargs[spec.name] = validator(value) if validator else value
            except (TypeError, ValueError) as exc:
                fallback = getattr(defaults, spec.name)
                warn(
                    f"settings.json: {spec.name} = {value!r} is invalid ({exc}); "
                    f"using the default {fallback!r} instead."
                )
        return cls(**kwargs)

    def save(self) -> None:
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self).items()
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# -- per-field coercion ------------------------------------------------------
#
# Each validator takes the raw JSON value and returns the typed value, or
# raises TypeError/ValueError with a reason a human can act on. Strings
# holding numbers are accepted ("8756" -> 8756) because that is the single
# most common hand-edit mistake; anything else falls back to the default.


def _as_path(value: Any) -> Path:
    if isinstance(value, Path):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty path string")
    return Path(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError("must be true or false")


def _as_int(value: Any, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError("must be a whole number, not a boolean")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        value = int(value.strip())
    if not isinstance(value, int):
        raise ValueError("must be a whole number")
    if minimum is not None and value < minimum:
        raise ValueError(f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"must be at most {maximum}")
    return value


def _as_float(value: Any, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError("must be a number, not a boolean")
    if isinstance(value, str):
        value = float(value.strip())
    if not isinstance(value, (int, float)):
        raise ValueError("must be a number")
    value = float(value)
    if value != value or not minimum <= value <= maximum:  # NaN never compares true
        raise ValueError(f"must be between {minimum} and {maximum}")
    return value


def _as_choice(value: Any, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value.strip().lower() not in choices:
        raise ValueError(f"must be one of {', '.join(choices)}")
    return value.strip().lower()


def _as_optional(inner: Callable[[Any], Any]) -> Callable[[Any], Any]:
    return lambda value: None if value is None else inner(value)


def _as_str(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value.strip()


def _as_keywords(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    if not isinstance(value, (list, tuple)):
        raise ValueError("must be a list of words")
    keywords = tuple(str(item).strip() for item in value if str(item).strip())
    return keywords


_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    **{name: _as_path for name in PATH_FIELDS},
    "model_size": lambda v: _as_choice(v, MODEL_SIZES),
    "device": lambda v: _as_choice(v, DEVICES),
    "cpu_threads": _as_optional(lambda v: _as_int(v, minimum=0, maximum=1024)),
    "condition_on_previous_text": _as_bool,
    "hallucination_silence_threshold": _as_optional(lambda v: _as_float(v, minimum=0.0, maximum=600.0)),
    "default_language": _as_str,
    "default_dialect": _as_optional(_as_str),
    "default_preset": _as_str,
    "default_burn": _as_bool,
    "default_translate": _as_bool,
    "silence_gap_seconds": lambda v: _as_float(v, minimum=0.1, maximum=10.0),
    "punch_mode": lambda v: _as_choice(v, PUNCH_MODES),
    "punch_zoom": lambda v: _as_float(v, minimum=1.0, maximum=2.0),
    "punch_duration_seconds": lambda v: _as_float(v, minimum=0.1, maximum=10.0),
    "punch_min_spacing_seconds": lambda v: _as_float(v, minimum=0.0, maximum=600.0),
    "punch_keywords": _as_keywords,
    "retention_days": lambda v: _as_int(v, minimum=0),
    "port": lambda v: _as_int(v, minimum=1024, maximum=65535),
    "min_free_disk_gb": lambda v: _as_float(v, minimum=0.0, maximum=100_000.0),
}


def recommended_model(device: Device) -> ModelSize:
    """Pick a model size the machine can actually run at a sane speed."""
    if device == "cuda":
        return "large-v3"
    # CPU: `small` is the honest default for EN/ES/PT short-form work. `medium`
    # is roughly 3x the weight for a modest accuracy gain and makes a laptop
    # crawl, so it stays a deliberate per-machine choice rather than a default.
    return "small"
