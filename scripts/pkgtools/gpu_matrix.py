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

The rule (spec section 11.2):
    ctranslate2 >= 4.5.0 requires cuDNN 9 + CUDA >= 12.3.
    Our GPU build pins exactly that combination (ctranslate2 4.5.x + the
    matching cuDNN 9 wheel) rather than trying to match wheels to whatever
    happens to be on a given machine.
    A driver's reported "CUDA Version" (from `nvidia-smi`) is the newest CUDA
    runtime it can support -- CUDA is backward compatible, so a driver
    reporting 12.3 or higher can run our pinned CUDA-12.3-built wheels. Below
    that, refuse: this is exactly the `cudnn_ops64_9.dll is not found`
    failure mode the spec warns about, and it must not reach an editor's
    machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The one combination our GPU build ships, per spec section 11.2.
PINNED_CTRANSLATE2_VERSION = "4.5.0"
PINNED_CUDNN_MAJOR = 9
MIN_DRIVER_CUDA_VERSION = (12, 3)

GPU_MODEL_SIZE = "large-v3"


@dataclass(frozen=True)
class GpuDecision:
    supported: bool
    reason: str
    driver_cuda_version: str | None = None

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


def evaluate_gpu_support(*, gpu_present: bool, driver_cuda_version: str | None) -> GpuDecision:
    """Decide whether this machine may be switched to `device=cuda`.

    `gpu_present` is `nvidia-smi` having run successfully and reported a GPU.
    `driver_cuda_version` is the raw "CUDA Version" string from `nvidia-smi`
    (or None if it could not be read).
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

    if parsed < MIN_DRIVER_CUDA_VERSION:
        min_str = ".".join(str(p) for p in MIN_DRIVER_CUDA_VERSION)
        got_str = ".".join(str(p) for p in parsed)
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

    got_str = ".".join(str(p) for p in parsed)
    return GpuDecision(
        supported=True,
        reason=(
            f"driver supports CUDA {got_str} >= required {'.'.join(str(p) for p in MIN_DRIVER_CUDA_VERSION)} "
            f"-- safe to install ctranslate2 {PINNED_CTRANSLATE2_VERSION} with cuDNN "
            f"{PINNED_CUDNN_MAJOR} and switch to device=cuda, model={GPU_MODEL_SIZE}"
        ),
        driver_cuda_version=driver_cuda_version,
    )
