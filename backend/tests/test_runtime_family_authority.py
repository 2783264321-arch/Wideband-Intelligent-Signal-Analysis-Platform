"""Final corrective: WSP_RUNTIME_FAMILY authority at local provider registration.

GPU REQUIRED: NO. Configuration-authority only; providers are never probed here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis.local_executor import build_local_providers
from app.core.config import Settings
from app.core.errors import PlatformError
from app.runtime_qualification.identity import validate_configured_local_runtime_ref

_HEX = "abcdefabcdef"
_CPU_AUTODL = f"local:autodl_primary:cpu:{_HEX}"
_GPU_AUTODL = f"local:autodl_primary:gpu:{_HEX}"
_CPU_OTHER = f"local:other_family:cpu:{_HEX}"
_GPU_OTHER = f"local:other_family:gpu:{_HEX}"


def _settings(**overrides) -> Settings:
    base = dict(
        runtime_family="autodl_primary",
        local_cpu_python_path=Path("cpu-python"),
        local_gpu_python_path=Path("gpu-python"),
    )
    base.update(overrides)
    return Settings(**base)


def test_family_mismatch_cpu_fails_closed() -> None:
    with pytest.raises(PlatformError):
        build_local_providers(_settings(runtime_family="other_family", local_cpu_runtime_ref=_CPU_AUTODL))


def test_family_mismatch_gpu_fails_closed() -> None:
    with pytest.raises(PlatformError):
        build_local_providers(_settings(runtime_family="other_family", local_gpu_runtime_ref=_GPU_AUTODL))


def test_wrong_kind_cpu_fails_closed() -> None:
    with pytest.raises(PlatformError):
        build_local_providers(_settings(local_cpu_runtime_ref=_GPU_AUTODL))


def test_wrong_kind_gpu_fails_closed() -> None:
    with pytest.raises(PlatformError):
        build_local_providers(_settings(local_gpu_runtime_ref=_CPU_AUTODL))


def test_malformed_ref_fails_closed() -> None:
    with pytest.raises(PlatformError):
        build_local_providers(_settings(local_cpu_runtime_ref="not-a-runtime-ref"))


def test_matching_family_and_kind_registers_providers() -> None:
    providers = build_local_providers(
        _settings(local_cpu_runtime_ref=_CPU_AUTODL, local_gpu_runtime_ref=_GPU_AUTODL)
    )
    assert set(providers) == {"local_cpu", "local_gpu"}
    assert providers["local_cpu"].runtime_ref == _CPU_AUTODL
    assert providers["local_gpu"].runtime_ref == _GPU_AUTODL


def test_runtime_family_missing_preserves_legacy_registration() -> None:
    providers = build_local_providers(
        _settings(runtime_family=None, local_cpu_runtime_ref=_CPU_AUTODL, local_gpu_runtime_ref=_GPU_OTHER)
    )
    assert set(providers) == {"local_cpu", "local_gpu"}


def test_validate_seam_directly() -> None:
    validate_configured_local_runtime_ref(executor="local_cpu", runtime_ref=_CPU_AUTODL, runtime_family="autodl_primary")
    validate_configured_local_runtime_ref(executor="local_cpu", runtime_ref=_CPU_OTHER, runtime_family=None)
    validate_configured_local_runtime_ref(executor="local_gpu", runtime_ref=_GPU_AUTODL, runtime_family="autodl_primary")

    for executor, ref, family in (
        ("local_cpu", _CPU_OTHER, "autodl_primary"),
        ("local_gpu", _GPU_OTHER, "autodl_primary"),
        ("local_cpu", _GPU_AUTODL, "autodl_primary"),
        ("local_gpu", _CPU_AUTODL, "autodl_primary"),
        ("local_cpu", "garbage", "autodl_primary"),
    ):
        with pytest.raises(PlatformError):
            validate_configured_local_runtime_ref(executor=executor, runtime_ref=ref, runtime_family=family)
