"""pyproject.toml, scripts/requirements-build.txt and the GPU matrix must
name the same ctranslate2 -- a drift between them is how a build box
silently ships a different CUDA/cuDNN contract than enable_gpu.ps1 checks.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from pkgtools.gpu_matrix import PINNED_CTRANSLATE2_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
REQUIREMENTS = REPO_ROOT / "scripts" / "requirements-build.txt"


def _project() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]


def _requirement(name: str, specs: list[str]) -> str:
    for spec in specs:
        if re.match(rf"{re.escape(name)}\s*[<>=!~\[]", spec):
            return spec
    raise AssertionError(f"{name} not in {specs}")


def test_runtime_dependencies_are_bounded():
    deps = _project()["dependencies"]
    assert "<2" in _requirement("faster-whisper", deps)
    ct2 = _requirement("ctranslate2", deps)
    assert f">={PINNED_CTRANSLATE2_VERSION}" in ct2 and "<5" in ct2


def test_dev_extras_cover_the_build_box():
    dev = _project()["optional-dependencies"]["dev"]
    assert _requirement("pyinstaller", dev)
    assert _requirement("huggingface_hub", dev)
    assert _requirement("pytest", dev)


def test_requirements_build_is_a_clean_pip_freeze():
    lines = [line.strip() for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()]
    pins = [line for line in lines if line and not line.startswith("#")]
    assert pins, "requirements-build.txt is empty"
    assert all("==" in line for line in pins), [line for line in pins if "==" not in line]
    assert not any(line.startswith("-e") or "ash-captions" in line.lower() for line in pins)
    assert f"ctranslate2=={PINNED_CTRANSLATE2_VERSION}" in pins
    assert any(line.startswith("faster-whisper==") for line in pins)
    assert any(line.startswith("pyinstaller==") for line in pins)
    assert any(line.startswith("huggingface_hub==") or line.startswith("huggingface-hub==") for line in pins)
