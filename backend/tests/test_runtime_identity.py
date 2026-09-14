"""Task 2: scheme-versioned runtime identity derivation/validation.

GPU REQUIRED: NO. The control plane never imports torch/ultralytics; material is
collected inside the configured ML interpreter through a fixed platform-owned
probe. The module holds no hard-coded historical runtime hash.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.runtime_qualification.identity import (
    BHQ3_GPU_V1,
    BHQ3_GPU_V1_FIELDS,
    LEGACY_OPAQUE,
    LOCAL_CPU_V1,
    LOCAL_CPU_V1_FIELDS,
    UNAVAILABLE,
    canonical_material_bytes,
    collect_identity_material,
    derive_generation_for_scheme,
    derive_local_runtime_ref,
    resolve_identity_scheme,
    validate_runtime_ref_against_material,
)

# The exact recorded BHQ-3 V1 material (sealed production identity).
BHQ3_MATERIAL = {
    "python": "3.12.3",
    "torch": "2.8.0+cu128",
    "torch_cuda": "12.8",
    "ultralytics": "8.4.114",
    "numpy": "2.3.2",
    "scipy": "1.18.0",
    "device_name": "NVIDIA GeForce RTX 5090",
    "compute_capability": "12.0",
    "driver_version": "580.105.08",
    "cuda_available": True,
}

_SEALED_CPU_REF = "local:autodl_primary:cpu:a1237f8faae7"


def _cpu_material(*, torch="2.4.0", ultralytics="8.2.0") -> dict:
    return {
        "python": "3.12.3",
        "platform_system": "Linux",
        "architecture": "x86_64",
        "torch": torch,
        "ultralytics": ultralytics,
        "numpy": "2.3.2",
        "scipy": "1.18.0",
    }


def test_bhq3_compatibility_reproduces_sealed_generation() -> None:
    assert BHQ3_GPU_V1_FIELDS == (
        "python", "torch", "torch_cuda", "ultralytics", "numpy",
        "scipy", "device_name", "compute_capability", "driver_version", "cuda_available",
    )
    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=BHQ3_MATERIAL)
    assert generation == "7b958347b5af"
    assert derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation) == (
        "local:autodl_primary:gpu:7b958347b5af"
    )


def test_no_extra_fields_in_v1_scheme() -> None:
    extra = dict(BHQ3_MATERIAL)
    extra["python_version"] = "3.12.3"
    with pytest.raises(PlatformError) as exc:
        derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=extra)
    assert exc.value.code == "RUNTIME_IDENTITY_INVALID"

    missing = {k: v for k, v in BHQ3_MATERIAL.items() if k != "scipy"}
    with pytest.raises(PlatformError) as exc2:
        derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=missing)
    assert exc2.value.code == "RUNTIME_IDENTITY_INVALID"


def test_cpu_material_includes_ml_versions_and_ignores_gpu_state() -> None:
    assert LOCAL_CPU_V1_FIELDS == (
        "python", "platform_system", "architecture", "torch", "ultralytics", "numpy", "scipy",
    )
    material = _cpu_material()
    first = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=material)
    second = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=dict(material))
    assert first == second

    # A GPU-only observation change cannot enter a CPU identity payload at all.
    leaked = dict(material)
    leaked["driver_version"] = "580.105.08"
    with pytest.raises(PlatformError) as exc:
        derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=leaked)
    assert exc.value.code == "RUNTIME_IDENTITY_INVALID"


def test_cpu_material_rotates_on_torch_and_ultralytics_change() -> None:
    base = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=_cpu_material())
    torch_changed = derive_generation_for_scheme(
        scheme=LOCAL_CPU_V1, material=_cpu_material(torch="2.5.0")
    )
    ultra_changed = derive_generation_for_scheme(
        scheme=LOCAL_CPU_V1, material=_cpu_material(ultralytics="8.3.0")
    )
    assert base != torch_changed
    assert base != ultra_changed
    assert torch_changed != ultra_changed


def test_legacy_opaque_comes_from_provenance_not_string_shape() -> None:
    provenance = frozenset({_SEALED_CPU_REF})

    # Known repo-default opaque CPU runtime + provenance, no new derivation.
    assert resolve_identity_scheme(
        executor="local_cpu",
        runtime_ref=_SEALED_CPU_REF,
        qualification_context=None,
        repo_default_runtime_refs=provenance,
        material_available=False,
    ) == LEGACY_OPAQUE

    # Same-shaped arbitrary ref without provenance is NOT legacy_opaque.
    same_shape = "local:autodl_primary:cpu:ffffffffffff"
    assert resolve_identity_scheme(
        executor="local_cpu",
        runtime_ref=same_shape,
        qualification_context=None,
        repo_default_runtime_refs=provenance,
        material_available=False,
    ) == UNAVAILABLE

    # A new local_cpu qualification derives local_cpu_v1.
    assert resolve_identity_scheme(
        executor="local_cpu",
        runtime_ref="local:autodl_primary:cpu:0123456789ab",
        qualification_context="qualify",
        repo_default_runtime_refs=provenance,
        material_available=True,
    ) == LOCAL_CPU_V1

    # Without material it is unavailable, never a legacy fallback.
    assert resolve_identity_scheme(
        executor="local_cpu",
        runtime_ref="local:autodl_primary:cpu:0123456789ab",
        qualification_context="qualify",
        repo_default_runtime_refs=provenance,
        material_available=False,
    ) == UNAVAILABLE

    # GPU compatibility identity is explicit.
    assert resolve_identity_scheme(
        executor="local_gpu",
        runtime_ref="local:autodl_primary:gpu:7b958347b5af",
        qualification_context=None,
        repo_default_runtime_refs=frozenset(),
        material_available=True,
    ) == BHQ3_GPU_V1


def test_new_cpu_material_not_reproducing_ref_is_mismatch_not_legacy() -> None:
    material = _cpu_material()
    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=material)
    correct_ref = derive_local_runtime_ref(family="autodl_primary", kind="cpu", generation=generation)
    validate_runtime_ref_against_material(
        runtime_ref=correct_ref,
        family="autodl_primary",
        kind="cpu",
        scheme=LOCAL_CPU_V1,
        material=material,
    )

    with pytest.raises(PlatformError) as exc:
        validate_runtime_ref_against_material(
            runtime_ref="local:autodl_primary:cpu:ffffffffffff",
            family="autodl_primary",
            kind="cpu",
            scheme=LOCAL_CPU_V1,
            material=material,
        )
    assert exc.value.code == "RUNTIME_IDENTITY_MISMATCH"


def test_validate_rejects_malformed_ref_and_family_drift() -> None:
    material = _cpu_material()
    for malformed in ("not-a-ref", "local:autodl_primary:cpu", "local:autodl_primary:cpu:XYZ"):
        with pytest.raises(PlatformError) as exc:
            validate_runtime_ref_against_material(
                runtime_ref=malformed,
                family="autodl_primary",
                kind="cpu",
                scheme=LOCAL_CPU_V1,
                material=material,
            )
        assert exc.value.code in {"RUNTIME_IDENTITY_INVALID", "RUNTIME_IDENTITY_MISMATCH"}

    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=material)
    ref = derive_local_runtime_ref(family="autodl_primary", kind="cpu", generation=generation)
    with pytest.raises(PlatformError) as exc:
        validate_runtime_ref_against_material(
            runtime_ref=ref,
            family="other_family",
            kind="cpu",
            scheme=LOCAL_CPU_V1,
            material=material,
        )
    assert exc.value.code == "RUNTIME_IDENTITY_MISMATCH"


def test_canonical_form_is_deterministic_and_order_independent() -> None:
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert canonical_material_bytes(a) == canonical_material_bytes(b)
    assert canonical_material_bytes(a) == json.dumps(
        {"a": 2, "b": 1}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_collect_identity_material_fails_closed_without_interpreter() -> None:
    with pytest.raises(PlatformError) as exc:
        collect_identity_material(None, scheme=LOCAL_CPU_V1)
    assert exc.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"

    with pytest.raises(PlatformError) as exc2:
        collect_identity_material(Path("/does/not/exist/python"), scheme=LOCAL_CPU_V1)
    assert exc2.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_identity_module_has_no_historical_hash_or_shape_branching() -> None:
    import app.runtime_qualification.identity as identity

    source = Path(identity.__file__).read_text(encoding="utf-8")
    assert "a1237f8faae7" not in source
    assert "7b958347b5af" not in source
    assert ".startswith(" not in source
