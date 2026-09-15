"""Task 4: qualification runner framework + real production LocalCpuTargetProbe.

GPU REQUIRED: NO. Deterministic fakes; no real inference, no GPU.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.assets import (
    PipelineAssetManifest,
    compute_asset_manifest_sha256,
)
from app.remote_execution.model_release import ModelRelease, ResolvedModelRelease
from app.remote_execution.runtime import RuntimeDescriptor
from app.remote_execution.source_hash import compute_file_sha256
from app.runtime_qualification.identity import (
    BHQ3_GPU_V1,
    LOCAL_CPU_V1,
    derive_generation_for_scheme,
    derive_local_runtime_ref,
)
from app.runtime_qualification.qualification import (
    GPU_DEFERRED,
    LOCAL_CPU_SMOKE_V1,
    LOCAL_GPU_CUDA_V1,
    DeferredGpuQualificationRunner,
    LocalCpuQualificationRunner,
    LocalCpuTargetProbe,
    LocalGpuQualificationRunner,
    LocalGpuTargetProbe,
    QualificationTarget,
    build_default_target_probe,
    qualification_type_install_eligible,
    run_qualification,
    select_runner,
)

_CLOCK = lambda: datetime(2026, 9, 14, tzinfo=timezone.utc)

_CPU_MATERIAL = {
    "python": "3.12.3",
    "platform_system": "Windows",
    "architecture": "AMD64",
    "torch": "2.4.0",
    "ultralytics": "8.2.0",
    "numpy": "2.3.2",
    "scipy": "1.18.0",
}


def _runtime_ref() -> str:
    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=_CPU_MATERIAL)
    return derive_local_runtime_ref(family="autodl_primary", kind="cpu", generation=generation)


def _definition(*, model_release_required: bool = False) -> PipelineDefinition:
    return PipelineDefinition(
        id="dummy",
        name="Dummy",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        model_release_required=model_release_required,
    )


class _Provider:
    name = "local_cpu"

    def __init__(self, *, runtime_ref: str, probe_ok: bool = True, device_type: str = "cpu", precision: str = "float32", name: str = "local_cpu") -> None:
        self.name = name
        self._runtime_ref = runtime_ref
        self._probe_ok = probe_ok
        self._device_type = device_type
        self._precision = precision

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=None,
            precision=self._precision,
            environment_ref="/ml/python",
            environment_label=self._runtime_ref,
        )

    def probe(self):
        return (self._probe_ok, None if self._probe_ok else "interpreter missing")


class _ReleaseStore:
    def __init__(self, resolved: ResolvedModelRelease | None = None) -> None:
        self._resolved = resolved

    def resolve(self, plugin_id: str, plugin_version: str, requested: str | None) -> ResolvedModelRelease:
        if self._resolved is None:
            raise PlatformError("MODEL_RELEASE_NOT_FOUND", "no release")
        return self._resolved


def _target(provider: _Provider, **overrides) -> QualificationTarget:
    payload = {
        "plugin_id": "dummy",
        "plugin_version": "1.0",
        "model_release_id": None,
        "executor": "local_cpu",
        "runtime_ref": provider.runtime_ref,
        "runtime_descriptor": provider.runtime_descriptor().to_metadata(),
    }
    payload.update(overrides)
    return QualificationTarget(**payload)


def _probe(definition, provider, release_store, *, material=None, settings=None) -> LocalCpuTargetProbe:
    return LocalCpuTargetProbe(
        settings=settings or Settings(runtime_family="autodl_primary"),
        definition=definition,
        provider=provider,
        model_release_store=release_store,
        material_probe=(material if material is not None else (lambda: dict(_CPU_MATERIAL))),
        now=_CLOCK,
    )


def test_local_cpu_passing_probe_yields_non_empty_pass() -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    runner = LocalCpuQualificationRunner(target_probe=_probe(definition, provider, _ReleaseStore()))
    results = runner.run(target=_target(provider))
    assert results and all(r.passed for r in results)
    assert runner.qualification_type == LOCAL_CPU_SMOKE_V1


def test_no_probe_never_passes() -> None:
    provider = _Provider(runtime_ref=_runtime_ref())
    runner = LocalCpuQualificationRunner(target_probe=None)
    results = runner.run(target=_target(provider))
    assert results[0].passed is False
    assert results[0].detail == "QUALIFICATION_PROBE_UNAVAILABLE"


def test_raising_probe_is_failed_not_exception() -> None:
    provider = _Provider(runtime_ref=_runtime_ref())

    def _boom(target):
        raise PlatformError("QUALIFICATION_PROBE_FAILED", "runtime_ref mismatch")

    runner = LocalCpuQualificationRunner(target_probe=_boom)
    results = runner.run(target=_target(provider))
    assert results[0].passed is False
    assert "runtime_ref mismatch" in (results[0].detail or "")


def test_deferred_gpu_never_installable() -> None:
    provider = _Provider(runtime_ref="remote:autodl_primary:abc", name="remote_gpu", device_type="cuda", precision="float16")
    runner = DeferredGpuQualificationRunner()
    results = runner.run(target=_target(provider, executor="remote_gpu"))
    assert results[0].passed is False
    assert runner.qualification_type == GPU_DEFERRED
    assert qualification_type_install_eligible(executor="remote_gpu", qualification_type=GPU_DEFERRED) is False


def test_install_eligibility_map() -> None:
    assert qualification_type_install_eligible(executor="local_cpu", qualification_type=LOCAL_CPU_SMOKE_V1) is True
    assert qualification_type_install_eligible(executor="local_cpu", qualification_type=LOCAL_GPU_CUDA_V1) is False
    assert qualification_type_install_eligible(executor="local_gpu", qualification_type=LOCAL_CPU_SMOKE_V1) is False
    assert qualification_type_install_eligible(executor="local_gpu", qualification_type=LOCAL_GPU_CUDA_V1) is True
    assert qualification_type_install_eligible(executor="local_gpu", qualification_type=GPU_DEFERRED) is False
    assert qualification_type_install_eligible(executor="remote_gpu", qualification_type=LOCAL_CPU_SMOKE_V1) is False
    assert qualification_type_install_eligible(executor="local_cpu", qualification_type="unknown_v9") is False


def test_select_runner_is_executor_based() -> None:
    assert isinstance(select_runner(executor="local_cpu", target_probe=lambda t: None), LocalCpuQualificationRunner)
    assert isinstance(select_runner(executor="local_gpu", target_probe=lambda t: None), LocalGpuQualificationRunner)
    assert isinstance(select_runner(executor="local_gpu", target_probe=None), LocalGpuQualificationRunner)
    # Plan C C-pre-1: remote_gpu now uses the real remote runner (not deferred).
    remote_runner = select_runner(executor="remote_gpu", target_probe=None)
    assert type(remote_runner).__name__ == "RemoteGpuQualificationRunner"
    assert remote_runner.qualification_type == "remote_gpu_probe_v1"


def test_run_qualification_echoes_identity(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    runner = LocalCpuQualificationRunner(target_probe=_probe(definition, provider, _ReleaseStore()))
    target = _target(provider)
    evidence = run_qualification(target=target, runner=runner, now=_CLOCK)
    assert evidence.plugin_id == target.plugin_id
    assert evidence.plugin_version == target.plugin_version
    assert evidence.runtime_ref == target.runtime_ref
    assert evidence.runtime_descriptor == target.runtime_descriptor
    assert evidence.qualification_type == LOCAL_CPU_SMOKE_V1
    assert evidence.passed is True
    assert not (tmp_path / "runtime_certificates.json").exists()


def test_build_default_target_probe_returns_real_probe() -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())

    class _Pipelines:
        def get(self, plugin_id):
            class _Handle:
                pass

            handle = _Handle()
            handle.definition = definition
            return handle

    class _Registry:
        def providers(self):
            return {"local_cpu": provider}

    probe = build_default_target_probe(
        target=_target(provider),
        settings=Settings(runtime_family="autodl_primary"),
        pipeline_registry=_Pipelines(),
        model_release_store=_ReleaseStore(),
        executor_registry=_Registry(),
        now=_CLOCK,
    )
    assert isinstance(probe, LocalCpuTargetProbe)
    assert probe is not None


def test_production_probe_enforces_every_check() -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    good = _probe(definition, provider, _ReleaseStore())
    good(_target(provider))  # happy path returns None

    # Missing provider.
    with pytest.raises(PlatformError):
        _probe(definition, None, _ReleaseStore())(_target(provider))

    # Wrong provider name.
    wrong_name = _Provider(runtime_ref=_runtime_ref(), name="local_gpu", device_type="cpu")
    with pytest.raises(PlatformError):
        _probe(definition, wrong_name, _ReleaseStore())(_target(wrong_name))

    # Provider probe failure.
    dead = _Provider(runtime_ref=_runtime_ref(), probe_ok=False)
    with pytest.raises(PlatformError):
        _probe(definition, dead, _ReleaseStore())(_target(dead))

    # runtime_ref mismatch.
    with pytest.raises(PlatformError):
        good(_target(provider, runtime_ref="local:autodl_primary:cpu:ffffffffffff"))

    # Descriptor mismatch.
    bad_desc = provider.runtime_descriptor().to_metadata()
    bad_desc["precision"] = "float16"
    with pytest.raises(PlatformError):
        good(_target(provider, runtime_descriptor=bad_desc))

    # Technical capability absent.
    no_cap = replace(
        definition,
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float16"),),
    )
    with pytest.raises(PlatformError):
        _probe(no_cap, provider, _ReleaseStore())(_target(provider))

    # Identity material mismatch.
    bad_material = dict(_CPU_MATERIAL)
    bad_material["torch"] = "9.9.9"
    with pytest.raises(PlatformError):
        _probe(definition, provider, _ReleaseStore(), material=lambda: bad_material)(_target(provider))

    # Plugin version mismatch.
    with pytest.raises(PlatformError):
        good(_target(provider, plugin_version="9.9"))


def test_production_probe_requires_runtime_family_and_cpu_kind() -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    good = _probe(definition, provider, _ReleaseStore())

    # runtime_family missing -> fail closed even for an otherwise-valid target.
    with pytest.raises(PlatformError):
        _probe(definition, provider, _ReleaseStore(), settings=Settings(runtime_family=None))(_target(provider))

    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=_CPU_MATERIAL)

    # kind must be exactly "cpu" for a local_cpu qualification.
    gpu_ref = derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation)
    gpu_provider = _Provider(runtime_ref=gpu_ref)
    with pytest.raises(PlatformError):
        _probe(definition, gpu_provider, _ReleaseStore())(_target(gpu_provider))

    # family must equal the operator-owned runtime_family.
    other_ref = derive_local_runtime_ref(family="other_family", kind="cpu", generation=generation)
    other_provider = _Provider(runtime_ref=other_ref)
    with pytest.raises(PlatformError):
        _probe(definition, other_provider, _ReleaseStore())(_target(other_provider))

    # valid family + kind=cpu preserved.
    good(_target(provider))


def test_production_probe_requires_exact_descriptor_executor() -> None:
    from dataclasses import replace as dc_replace

    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    miswired = _Provider(runtime_ref=_runtime_ref())
    original = miswired.runtime_descriptor
    miswired.runtime_descriptor = lambda: dc_replace(original(), executor="local_gpu")
    with pytest.raises(PlatformError):
        _probe(definition, miswired, _ReleaseStore())(_target(miswired))
    _probe(definition, provider, _ReleaseStore())(_target(provider))


def test_production_probe_release_semantics(tmp_path: Path) -> None:
    asset_file = tmp_path / "model.bin"
    asset_file.write_bytes(b"payload")
    sha = compute_file_sha256(asset_file)
    manifest = PipelineAssetManifest(
        pipeline_id="cpn", pipeline_version="1.0.0", assets={"checkpoint": sha}, asset_manifest_sha256=""
    )
    manifest = replace(manifest, asset_manifest_sha256=compute_asset_manifest_sha256(manifest))
    release = ModelRelease(
        plugin_id="cpn", plugin_version="1.0.0", model_release_id="golden",
        asset_manifest_path=tmp_path / "manifest.json", asset_manifest_sha256=manifest.asset_manifest_sha256,
    )
    resolved = ResolvedModelRelease(release=release, manifest=manifest)

    definition = PipelineDefinition(
        id="cpn", name="CPN", version="1.0.0", label_space="spacenet_14", recommended_device="CPU",
        cpu_supported=True, stages=(), inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        model_release_required=True,
    )
    provider = _Provider(runtime_ref=_runtime_ref())
    namespace = f"cpn/1.0.0/{manifest.asset_manifest_sha256}"
    settings = Settings(
        runtime_family="autodl_primary",
        local_asset_paths={namespace: {"checkpoint": str(asset_file)}},
    )

    probe = _probe(definition, provider, _ReleaseStore(resolved), settings=settings)
    probe(_target(provider, plugin_id="cpn", plugin_version="1.0.0", model_release_id="golden"))

    # Release mismatch for a release-required plugin.
    with pytest.raises(PlatformError):
        probe(_target(provider, plugin_id="cpn", plugin_version="1.0.0", model_release_id="other"))

    # Asset SHA failure.
    tamper = tmp_path / "tampered.bin"
    tamper.write_bytes(b"tampered")
    bad_settings = Settings(
        runtime_family="autodl_primary",
        local_asset_paths={namespace: {"checkpoint": str(tamper)}},
    )
    with pytest.raises(PlatformError):
        _probe(definition, provider, _ReleaseStore(resolved), settings=bad_settings)(
            _target(provider, plugin_id="cpn", plugin_version="1.0.0", model_release_id="golden")
        )

    # A release-less plugin must not carry a release identity.
    with pytest.raises(PlatformError):
        _probe(_definition(), provider, _ReleaseStore())(
            _target(provider, model_release_id="golden")
        )


# ---------------------------------------------------------------------------
# B1: local_gpu qualification (deterministic fakes; no GPU, no inference)
# ---------------------------------------------------------------------------

_GPU_MATERIAL = {
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


def _gpu_runtime_ref() -> str:
    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=_GPU_MATERIAL)
    return derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation)


def _gpu_definition(*, model_release_required: bool = False) -> PipelineDefinition:
    return PipelineDefinition(
        id="dummy",
        name="Dummy",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_gpu", "cuda", "float16"),),
        model_release_required=model_release_required,
    )


class _GpuProvider:
    def __init__(self, *, runtime_ref: str, probe_ok: bool = True,
                 device_type: str = "cuda", precision: str = "float16",
                 name: str = "local_gpu", device_index: int | None = 0) -> None:
        self.name = name
        self._runtime_ref = runtime_ref
        self._probe_ok = probe_ok
        self._device_type = device_type
        self._precision = precision
        self._device_index = device_index

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=self._device_index,
            precision=self._precision,
            environment_ref="/root/miniconda3/bin/python",
            environment_label=self._runtime_ref,
        )

    def probe(self):
        return (self._probe_ok, None if self._probe_ok else "cuda probe failed")


def _gpu_target(provider: _GpuProvider, **overrides) -> QualificationTarget:
    payload = {
        "plugin_id": "dummy",
        "plugin_version": "1.0",
        "model_release_id": None,
        "executor": "local_gpu",
        "runtime_ref": provider.runtime_ref,
        "runtime_descriptor": provider.runtime_descriptor().to_metadata(),
    }
    payload.update(overrides)
    return QualificationTarget(**payload)


def _gpu_probe(definition, provider, release_store, *, material=None, settings=None) -> LocalGpuTargetProbe:
    return LocalGpuTargetProbe(
        settings=settings or Settings(runtime_family="autodl_primary"),
        definition=definition,
        provider=provider,
        model_release_store=release_store,
        material_probe=(material if material is not None else (lambda: dict(_GPU_MATERIAL))),
        now=_CLOCK,
    )


def test_local_gpu_passing_probe_yields_non_empty_pass() -> None:
    definition = _gpu_definition()
    provider = _GpuProvider(runtime_ref=_gpu_runtime_ref())
    runner = LocalGpuQualificationRunner(target_probe=_gpu_probe(definition, provider, _ReleaseStore()))
    results = runner.run(target=_gpu_target(provider))
    assert results and all(r.passed for r in results)
    assert runner.qualification_type == LOCAL_GPU_CUDA_V1
    assert runner.identity_scheme == BHQ3_GPU_V1
    assert qualification_type_install_eligible(executor="local_gpu", qualification_type=LOCAL_GPU_CUDA_V1) is True


def test_local_gpu_runner_without_probe_never_passes() -> None:
    provider = _GpuProvider(runtime_ref=_gpu_runtime_ref())
    runner = LocalGpuQualificationRunner(target_probe=None)
    results = runner.run(target=_gpu_target(provider))
    assert results[0].passed is False
    assert results[0].detail == "QUALIFICATION_PROBE_UNAVAILABLE"


def test_local_gpu_runner_probe_failure_is_failed_not_exception() -> None:
    provider = _GpuProvider(runtime_ref=_gpu_runtime_ref())

    def _boom(target):
        raise PlatformError("QUALIFICATION_PROBE_FAILED", "cuda probe failed")

    runner = LocalGpuQualificationRunner(target_probe=_boom)
    results = runner.run(target=_gpu_target(provider))
    assert results[0].passed is False
    assert "cuda probe failed" in (results[0].detail or "")


def test_local_gpu_target_probe_enforces_every_check() -> None:
    definition = _gpu_definition()
    provider = _GpuProvider(runtime_ref=_gpu_runtime_ref())
    good = _gpu_probe(definition, provider, _ReleaseStore())
    good(_gpu_target(provider))  # happy path returns None

    # Executor mismatch.
    with pytest.raises(PlatformError):
        good(_gpu_target(provider, executor="local_cpu"))

    # Missing provider.
    with pytest.raises(PlatformError):
        _gpu_probe(definition, None, _ReleaseStore())(_gpu_target(provider))

    # Wrong provider name.
    wrong_name = _GpuProvider(runtime_ref=_gpu_runtime_ref(), name="local_cpu", device_type="cuda")
    with pytest.raises(PlatformError):
        _gpu_probe(definition, wrong_name, _ReleaseStore())(_gpu_target(wrong_name))

    # Provider probe failure.
    dead = _GpuProvider(runtime_ref=_gpu_runtime_ref(), probe_ok=False)
    with pytest.raises(PlatformError):
        _gpu_probe(definition, dead, _ReleaseStore())(_gpu_target(dead))

    # Non-cuda device type.
    cpu_device = _GpuProvider(runtime_ref=_gpu_runtime_ref(), device_type="cpu")
    with pytest.raises(PlatformError):
        _gpu_probe(definition, cpu_device, _ReleaseStore())(_gpu_target(cpu_device))

    # runtime_ref mismatch.
    with pytest.raises(PlatformError):
        good(_gpu_target(provider, runtime_ref="local:autodl_primary:gpu:ffffffffffff"))

    # Descriptor mismatch.
    bad_desc = provider.runtime_descriptor().to_metadata()
    bad_desc["precision"] = "float32"
    with pytest.raises(PlatformError):
        good(_gpu_target(provider, runtime_descriptor=bad_desc))

    # Technical capability absent.
    no_cap = replace(
        definition,
        technical_execution_capabilities=(ExecutionCapability("local_gpu", "cuda", "float32"),),
    )
    with pytest.raises(PlatformError):
        _gpu_probe(no_cap, provider, _ReleaseStore())(_gpu_target(provider))

    # runtime_family missing.
    with pytest.raises(PlatformError):
        _gpu_probe(definition, provider, _ReleaseStore(), settings=Settings(runtime_family=None))(
            _gpu_target(provider)
        )

    # kind must be gpu.
    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=_GPU_MATERIAL)
    cpu_kind_ref = derive_local_runtime_ref(family="autodl_primary", kind="cpu", generation=generation)
    cpu_kind = _GpuProvider(runtime_ref=cpu_kind_ref)
    with pytest.raises(PlatformError):
        _gpu_probe(definition, cpu_kind, _ReleaseStore())(_gpu_target(cpu_kind))

    # family must match the operator-owned runtime_family.
    other_ref = derive_local_runtime_ref(family="other_family", kind="gpu", generation=generation)
    other = _GpuProvider(runtime_ref=other_ref)
    with pytest.raises(PlatformError):
        _gpu_probe(definition, other, _ReleaseStore())(_gpu_target(other))

    # Identity material mismatch.
    bad_material = dict(_GPU_MATERIAL)
    bad_material["driver_version"] = "999.0"
    with pytest.raises(PlatformError):
        _gpu_probe(definition, provider, _ReleaseStore(), material=lambda: bad_material)(
            _gpu_target(provider)
        )

    # Plugin version mismatch.
    with pytest.raises(PlatformError):
        good(_gpu_target(provider, plugin_version="9.9"))


def test_build_default_target_probe_selects_local_gpu() -> None:
    definition = _gpu_definition()
    provider = _GpuProvider(runtime_ref=_gpu_runtime_ref())

    class _Pipelines:
        def get(self, plugin_id):
            class _Handle:
                pass

            handle = _Handle()
            handle.definition = definition
            return handle

    class _Registry:
        def providers(self):
            return {"local_gpu": provider}

    probe = build_default_target_probe(
        target=_gpu_target(provider),
        settings=Settings(runtime_family="autodl_primary"),
        pipeline_registry=_Pipelines(),
        model_release_store=_ReleaseStore(),
        executor_registry=_Registry(),
        now=_CLOCK,
    )
    assert isinstance(probe, LocalGpuTargetProbe)


def test_local_gpu_probe_release_semantics(tmp_path: Path) -> None:
    asset_file = tmp_path / "model.bin"
    asset_file.write_bytes(b"gpu-payload")
    sha = compute_file_sha256(asset_file)
    manifest = PipelineAssetManifest(
        pipeline_id="cpn", pipeline_version="1.0.0", assets={"checkpoint": sha}, asset_manifest_sha256=""
    )
    manifest = replace(manifest, asset_manifest_sha256=compute_asset_manifest_sha256(manifest))
    release = ModelRelease(
        plugin_id="cpn", plugin_version="1.0.0", model_release_id="golden",
        asset_manifest_path=tmp_path / "manifest.json", asset_manifest_sha256=manifest.asset_manifest_sha256,
    )
    resolved = ResolvedModelRelease(release=release, manifest=manifest)

    definition = replace(
        _gpu_definition(model_release_required=True), id="cpn", version="1.0.0"
    )
    provider = _GpuProvider(runtime_ref=_gpu_runtime_ref())
    namespace = f"cpn/1.0.0/{manifest.asset_manifest_sha256}"
    settings = Settings(
        runtime_family="autodl_primary",
        local_asset_paths={namespace: {"checkpoint": str(asset_file)}},
    )
    probe = _gpu_probe(definition, provider, _ReleaseStore(resolved), settings=settings)
    probe(_gpu_target(provider, plugin_id="cpn", plugin_version="1.0.0", model_release_id="golden"))

    # Release mismatch.
    with pytest.raises(PlatformError):
        probe(_gpu_target(provider, plugin_id="cpn", plugin_version="1.0.0", model_release_id="other"))

    # Asset SHA failure.
    tamper = tmp_path / "tampered.bin"
    tamper.write_bytes(b"tampered")
    bad_settings = Settings(
        runtime_family="autodl_primary",
        local_asset_paths={namespace: {"checkpoint": str(tamper)}},
    )
    with pytest.raises(PlatformError):
        _gpu_probe(definition, provider, _ReleaseStore(resolved), settings=bad_settings)(
            _gpu_target(provider, plugin_id="cpn", plugin_version="1.0.0", model_release_id="golden")
        )

    # Release-less plugin must not carry a release identity.
    with pytest.raises(PlatformError):
        _gpu_probe(_gpu_definition(), provider, _ReleaseStore())(
            _gpu_target(provider, model_release_id="golden")
        )
