"""The ctranslate2 CUDA/cuDNN compatibility decision -- pure Python mirror.

`scripts/enable_gpu.ps1` is the script Ghazi actually runs on an editor's
machine, and it re-implements this same table natively in PowerShell (see the
comment block at its top) so it has zero Python dependency on a machine where
we deliberately have not installed our app's Python yet. This module exists so
the *decision table itself* -- the part most likely to be gotten subtly wrong,
and the part spec section 11.2 calls out by name -- has a fast, direct unit
test, independent of shelling out to PowerShell.

`tests/test_packaging/test_enable_gpu_ps1.py` then separately drives the real
`.ps1` script via its `-CheckOnly` mode and asserts the same outcomes, so the
two implementations are cross-checked.

The rule (spec section 11.2, updated for what the venv actually ships):
    ctranslate2 4.8.x (the pinned build) requires cuDNN 9 + cuBLAS 12 and a
    driver supporting CUDA >= 12.3.
    A driver's reported "CUDA Version" (from `nvidia-smi`) is the newest CUDA
    runtime it can support -- CUDA is backward compatible, so a driver
    reporting 12.3 or higher can run our CUDA-12-built wheels. Below that,
    refuse.

    The driver check alone is not enough. The CPU bundle we ship contains no
    cuBLAS and only the thin `cudnn64_9.dll` loader that comes inside the
    ctranslate2 wheel: `ctranslate2.dll` loads `cublas64_12.dll` at runtime,
    and that loader in turn pulls in the `cudnn_*64_9.dll` sub-libraries.
    Flipping `device=cuda` without those DLLs present reproduces the exact
    `cudnn_ops64_9.dll is not found` failure at the first job. So the second
    half of the decision is: every DLL in REQUIRED_CUDA_DLLS must be beside
    `AshCaptions.exe` (or on PATH). Until a GPU build ships them, this refuses
    on every machine -- which is the correct, honest answer.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# The one combination our GPU build ships, per spec section 11.2. Keep in
# lockstep with the venv (`pip list`) and `pyproject.toml`'s ctranslate2 pin.
PINNED_CTRANSLATE2_VERSION = "4.8.2"
PINNED_CUDNN_MAJOR = 9
PINNED_CUBLAS_MAJOR = 12
MIN_DRIVER_CUDA_VERSION = (12, 3)

GPU_MODEL_SIZE = "large-v3"

# What ctranslate2 4.8.2's Windows wheel loads dynamically (`ctranslate2.dll`
# names `cublas64_12.dll`; its bundled `cudnn64_9.dll` is NVIDIA's loader
# shim, which resolves the cuDNN 9 sub-libraries by name at first use).
# `nvcuda.dll` is the driver's and is implied by nvidia-smi being present.
REQUIRED_CUDA_DLLS: tuple[str, ...] = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_heuristic64_9.dll",
)


@dataclass(frozen=True)
class GpuDecision:
    supported: bool
    reason: str
    driver_cuda_version: str | None = None
    missing_dlls: tuple[str, ...] = ()

    @property
    def recommended_model(self) -> str | None:
        return GPU_MODEL_SIZE if self.supported else None


def parse_driver_cuda_version(text: str) -> tuple[int, int] | None:
    """Extract a (major, minor) pair from the "CUDA Version" nvidia-smi
    reports (e.g. "12.4" out of the banner, or a bare "12.4" from
    `--query-gpu`). Returns None if nothing parses."""
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def find_missing_cuda_dlls(
    search_dirs: Iterable[str | os.PathLike[str]],
    *,
    required: Sequence[str] = REQUIRED_CUDA_DLLS,
) -> tuple[str, ...]:
    """The required DLLs that exist in none of `search_dirs` (case-
    insensitively -- Windows filenames are). Pure filesystem check."""
    present: set[str] = set()
    for directory in search_dirs:
        path = Path(directory)
        if not path.is_dir():
            continue
        try:
            present.update(p.name.lower() for p in path.iterdir())
        except OSError:
            continue
    return tuple(name for name in required if name.lower() not in present)


def evaluate_gpu_support(
    *,
    gpu_present: bool,
    driver_cuda_version: str | None,
    missing_dlls: Sequence[str] = (),
) -> GpuDecision:
    """Decide whether this machine may be switched to `device=cuda`.

    `gpu_present` is `nvidia-smi` having run successfully and reported a GPU.
    `driver_cuda_version` is the raw "CUDA Version" string from `nvidia-smi`
    (or None if it could not be read). `missing_dlls` is the output of
    `find_missing_cuda_dlls` for the install directory and PATH.
    """
    if not gpu_present:
        return GpuDecision(
            supported=False,
            reason="no NVIDIA GPU detected (nvidia-smi did not report one) -- staying on CPU",
        )

    parsed = parse_driver_cuda_version(driver_cuda_version or "")
    if parsed is None:
        return GpuDecision(
            supported=False,
            reason=(
                "could not read a CUDA version from nvidia-smi -- refusing to guess; "
                "update the NVIDIA driver and re-run"
            ),
            driver_cuda_version=driver_cuda_version,
        )

    min_str = ".".join(str(p) for p in MIN_DRIVER_CUDA_VERSION)
    got_str = ".".join(str(p) for p in parsed)
    if parsed < MIN_DRIVER_CUDA_VERSION:
        return GpuDecision(
            supported=False,
            reason=(
                f"driver supports CUDA {got_str}, but ctranslate2 {PINNED_CTRANSLATE2_VERSION} "
                f"needs cuDNN {PINNED_CUDNN_MAJOR} + CUDA >= {min_str}. Installing anyway "
                f"reproduces the 'cudnn_ops64_9.dll is not found' failure at the first job. "
                f"Update the NVIDIA driver to a version reporting CUDA {min_str} or newer, "
                f"then re-run this script."
            ),
            driver_cuda_version=driver_cuda_version,
        )

    missing = tuple(missing_dlls)
    if missing:
        return GpuDecision(
            supported=False,
            reason=(
                f"driver supports CUDA {got_str}, but the installed bundle has no CUDA "
                f"runtime: missing {', '.join(missing)}. The CPU build ships no cuBLAS "
                f"{PINNED_CUBLAS_MAJOR} / cuDNN {PINNED_CUDNN_MAJOR} libraries, so device=cuda "
                f"would fail at the first job with 'cudnn_ops64_9.dll is not found'. Copy the "
                f"DLLs from NVIDIA's nvidia-cublas-cu12 and nvidia-cudnn-cu12 wheels (their "
                f"bin/ folders) beside AshCaptions.exe, then re-run this script."
            ),
            driver_cuda_version=driver_cuda_version,
            missing_dlls=missing,
        )

    return GpuDecision(
        supported=True,
        reason=(
            f"driver supports CUDA {got_str} >= required {min_str} and every cuBLAS "
            f"{PINNED_CUBLAS_MAJOR} / cuDNN {PINNED_CUDNN_MAJOR} DLL ctranslate2 "
            f"{PINNED_CTRANSLATE2_VERSION} loads is present -- safe to switch to "
            f"device=cuda, model={GPU_MODEL_SIZE}"
        ),
        driver_cuda_version=driver_cuda_version,
    )
