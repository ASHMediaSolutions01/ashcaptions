"""Drives the real scripts/enable_gpu.ps1 via its -CheckOnly mode and
cross-checks it against the pure-Python decision table in
pkgtools/gpu_matrix.py -- both implementations must refuse the same
insufficient-CUDA cases the spec warns about.

No network, no actual GPU required: -SimulateCudaVersion / -SimulateNoGpu
bypass the real nvidia-smi call entirely.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path

import pytest
from pkgtools.gpu_matrix import evaluate_gpu_support

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enable_gpu.ps1"

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows" or shutil.which("powershell") is None,
    reason="enable_gpu.ps1 is a Windows PowerShell script",
)


def _run_check_only(*extra_args: str) -> dict:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_PATH),
            "-CheckOnly",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return json.loads(result.stdout)


def test_script_exists():
    assert SCRIPT_PATH.is_file()


def test_no_gpu_refused():
    payload = _run_check_only("-SimulateNoGpu")
    assert payload["gpu"]["Present"] is False
    assert payload["decision"]["supported"] is False


@pytest.mark.parametrize("cuda_version", ["11.8", "12.0", "12.2"])
def test_insufficient_cuda_refused(cuda_version):
    payload = _run_check_only("-SimulateCudaVersion", cuda_version)
    assert payload["gpu"]["Present"] is True
    assert payload["decision"]["supported"] is False
    assert "cudnn_ops64_9.dll" in payload["decision"]["reason"]


@pytest.mark.parametrize("cuda_version", ["12.3", "12.4", "13.0"])
def test_sufficient_cuda_allowed(cuda_version):
    payload = _run_check_only("-SimulateCudaVersion", cuda_version)
    assert payload["decision"]["supported"] is True


@pytest.mark.parametrize("cuda_version", ["11.8", "12.2", "12.3", "12.4", "13.0"])
def test_agrees_with_python_decision_table(cuda_version):
    """Cross-check: the PowerShell script actually run on an editor's
    machine must agree with pkgtools/gpu_matrix.py, which is what
    tests/test_packaging/test_gpu_matrix.py exhaustively unit-tests."""
    ps_payload = _run_check_only("-SimulateCudaVersion", cuda_version)
    py_decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version=cuda_version)
    assert ps_payload["decision"]["supported"] == py_decision.supported


def test_check_only_does_not_write_settings(tmp_path):
    settings_path = tmp_path / "settings.json"
    _run_check_only("-SimulateCudaVersion", "12.4", "-SettingsPath", str(settings_path))
    assert not settings_path.exists()


def test_success_writes_settings_only_on_non_check_run(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"device": "cpu", "model_size": "small", "port": 8756}', encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT_PATH),
            "-SimulateCudaVersion", "12.4", "-SettingsPath", str(settings_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["device"] == "cuda"
    assert payload["model_size"] == "large-v3"
    assert payload["port"] == 8756  # untouched fields survive


def test_refusal_exits_nonzero_and_does_not_write_settings(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"device": "cpu", "model_size": "small"}', encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT_PATH),
            "-SimulateCudaVersion", "12.0", "-SettingsPath", str(settings_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["device"] == "cpu"  # refused -- config left untouched
