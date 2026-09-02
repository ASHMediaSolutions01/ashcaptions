"""Tests for scripts/pkgtools/gpu_matrix.py: the ctranslate2 CUDA/cuDNN
decision table. This is the part spec section 11.2 calls out as the single
most likely thing to turn rollout into a support week, so the REFUSE cases
matter as much as the ALLOW case.

`tests/test_packaging/test_enable_gpu_ps1.py` drives the real PowerShell
script this module mirrors, and checks it agrees on the same cases.
"""

from __future__ import annotations

import pytest
from pkgtools.gpu_matrix import (
    MIN_DRIVER_CUDA_VERSION,
    evaluate_gpu_support,
    parse_driver_cuda_version,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12.4", (12, 4)),
        ("CUDA Version: 12.3", (12, 3)),
        ("11.8", (11, 8)),
        ("", None),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_driver_cuda_version(text, expected):
    assert parse_driver_cuda_version(text) == expected


def test_no_gpu_present_is_refused():
    decision = evaluate_gpu_support(gpu_present=False, driver_cuda_version=None)
    assert decision.supported is False
    assert decision.recommended_model is None
    assert "no NVIDIA GPU" in decision.reason


def test_unreadable_cuda_version_is_refused():
    decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version=None)
    assert decision.supported is False
    assert "could not read" in decision.reason.lower()


@pytest.mark.parametrize("version", ["11.8", "12.0", "12.1", "12.2"])
def test_insufficient_cuda_version_is_refused(version):
    """The exact failure mode the spec warns about: an old driver must be
    REFUSED, not silently allowed through to a cudnn_ops64_9.dll crash."""
    decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version=version)
    assert decision.supported is False
    assert decision.recommended_model is None
    assert "cudnn_ops64_9.dll" in decision.reason


@pytest.mark.parametrize("version", ["12.3", "12.4", "12.6", "13.0"])
def test_sufficient_cuda_version_is_allowed(version):
    decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version=version)
    assert decision.supported is True
    assert decision.recommended_model == "large-v3"


def test_boundary_matches_documented_minimum():
    # The documented threshold itself must be allowed, not treated as "below".
    boundary = ".".join(str(p) for p in MIN_DRIVER_CUDA_VERSION)
    decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version=boundary)
    assert decision.supported is True

    just_below = f"{MIN_DRIVER_CUDA_VERSION[0]}.{MIN_DRIVER_CUDA_VERSION[1] - 1}"
    decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version=just_below)
    assert decision.supported is False


# -- review item 7: the DLL half of the decision ---------------------------


def test_missing_cuda_dlls_refuse_even_with_a_good_driver():
    from pkgtools.gpu_matrix import REQUIRED_CUDA_DLLS

    decision = evaluate_gpu_support(
        gpu_present=True, driver_cuda_version="12.6", missing_dlls=("cublas64_12.dll", "cudnn_ops64_9.dll")
    )
    assert decision.supported is False
    assert decision.recommended_model is None
    assert "cudnn_ops64_9.dll" in decision.reason
    assert "cublas64_12.dll" in decision.reason
    assert decision.missing_dlls == ("cublas64_12.dll", "cudnn_ops64_9.dll")
    assert set(decision.missing_dlls) <= set(REQUIRED_CUDA_DLLS)


def test_driver_too_old_is_reported_before_dlls():
    decision = evaluate_gpu_support(gpu_present=True, driver_cuda_version="12.0", missing_dlls=("cublas64_12.dll",))
    assert decision.supported is False
    assert "Update the NVIDIA driver" in decision.reason


def test_find_missing_cuda_dlls_is_case_insensitive_and_searches_every_dir(tmp_path):
    from pkgtools.gpu_matrix import REQUIRED_CUDA_DLLS, find_missing_cuda_dlls

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for name in REQUIRED_CUDA_DLLS[:3]:
        (a / name.upper()).write_bytes(b"")
    for name in REQUIRED_CUDA_DLLS[3:]:
        (b / name).write_bytes(b"")
    assert find_missing_cuda_dlls([a, b, tmp_path / "does-not-exist"]) == ()
    assert find_missing_cuda_dlls([a]) == REQUIRED_CUDA_DLLS[3:]
    assert find_missing_cuda_dlls([]) == REQUIRED_CUDA_DLLS


def test_required_dll_list_covers_what_ctranslate2_loads():
    from pkgtools.gpu_matrix import REQUIRED_CUDA_DLLS

    # ctranslate2.dll names cublas64_12.dll; its bundled cudnn64_9.dll shim
    # resolves the cudnn_*64_9 sub-libraries -- the classic missing one first.
    assert "cublas64_12.dll" in REQUIRED_CUDA_DLLS
    assert "cudnn_ops64_9.dll" in REQUIRED_CUDA_DLLS
    assert all(name.endswith(".dll") for name in REQUIRED_CUDA_DLLS)


def test_pinned_ctranslate2_matches_the_installed_wheel():
    from pkgtools.gpu_matrix import PINNED_CTRANSLATE2_VERSION

    ctranslate2 = pytest.importorskip("ctranslate2")
    assert ctranslate2.__version__ == PINNED_CTRANSLATE2_VERSION
