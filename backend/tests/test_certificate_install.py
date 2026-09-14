"""Task 5: live-authority certificate install + operator store.

GPU REQUIRED: NO. Filesystem + deterministic fakes only.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.runtime import (
    ExecutionCertificateStore,
    ExecutorRegistry,
    load_execution_certificates,
)
from app.runtime_qualification.evidence import (
    QualificationEvidence,
    QualificationResult,
)
from app.runtime_qualification.identity import (
    LOCAL_CPU_V1,
    derive_generation_for_scheme,
    derive_local_runtime_ref,
)
from app.runtime_qualification.install import (
    LiveAuthority,
    build_certificate_store,
    install_certificate,
    load_operator_certificates,
    operator_certificate_path,
    resolve_live_authority,
    validate_evidence_for_install,
)
from app.runtime_qualification.qualification import (
    LocalCpuQualificationRunner,
    LocalCpuTargetProbe,
    QualificationTarget,
    run_qualification,
)

_CLOCK = lambda: datetime(2026, 9, 14, tzinfo=timezone.utc)

_REPO_PATH = Path(__file__).resolve().parents[1] / "app" / "pipelines" / "execution_certificates.json"

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
    def __init__(self, *, runtime_ref: str, name: str = "local_cpu", device_type: str = "cpu", precision: str = "float32") -> None:
        self.name = name
        self._runtime_ref = runtime_ref
        self._device_type = device_type
        self._precision = precision

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self):
        from app.remote_execution.runtime import RuntimeDescriptor

        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=None,
            precision=self._precision,
            environment_ref="/ml/python",
            environment_label=self._runtime_ref,
        )

    def probe(self):
        return (True, None)


class _ReleaseStore:
    def resolve(self, plugin_id, plugin_version, requested):
        raise PlatformError("MODEL_RELEASE_NOT_FOUND", "no releases in this test")


class _Pipelines:
    def __init__(self, definition: PipelineDefinition) -> None:
        self._definition = definition

    def get(self, plugin_id: str):
        if plugin_id != self._definition.plugin_id:
            raise PlatformError("PIPELINE_INCOMPATIBLE", "unknown plugin")

        class _Handle:
            definition = self._definition

        return _Handle()


class _ExecRegistry:
    def __init__(self, providers) -> None:
        self._providers = dict(providers)

    def providers(self):
        return dict(self._providers)


def _empty_repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo_certificates.json"
    path.write_text(json.dumps({"certificates": []}), encoding="utf-8")
    return path


def _evidence(provider: _Provider, definition: PipelineDefinition, settings: Settings) -> QualificationEvidence:
    probe = LocalCpuTargetProbe(
        settings=settings,
        definition=definition,
        provider=provider,
        model_release_store=_ReleaseStore(),
        material_probe=lambda: dict(_CPU_MATERIAL),
        now=_CLOCK,
    )
    target = QualificationTarget(
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        model_release_id=None,
        executor="local_cpu",
        runtime_ref=provider.runtime_ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )
    return run_qualification(
        target=target, runner=LocalCpuQualificationRunner(target_probe=probe), now=_CLOCK
    )


def _authority(definition, provider, tmp_path: Path) -> LiveAuthority:
    return resolve_live_authority(
        registry=_Pipelines(definition),
        model_release_store=_ReleaseStore(),
        executor_registry=_ExecRegistry({"local_cpu": provider} if provider else {}),
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        executor="local_cpu",
        requested_model_release_id=None,
        data_root=tmp_path,
    )


def test_live_authority_is_technical_not_certification(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    authority = _authority(definition, provider, tmp_path)
    assert authority.technical_capability_present is True
    assert authority.provider_present is True
    settings = Settings(runtime_family="autodl_primary")
    evidence = _evidence(provider, definition, settings)
    validate_evidence_for_install(evidence=evidence, authority=authority)

    # runtime_ref differs
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=replace(authority, runtime_ref="local:autodl_primary:cpu:ffffffffffff"))
    # descriptor differs
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=replace(authority, runtime_descriptor={**authority.runtime_descriptor, "precision": "float16"}))
    # plugin version differs
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=replace(authority, plugin_version="9.9"))
    # provider missing
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=replace(authority, provider_present=False))
    # technical capability absent
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=replace(authority, technical_capability_present=False))


def test_qualification_alone_never_writes_certificate(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    settings = Settings(runtime_family="autodl_primary")
    _evidence(provider, definition, settings)
    assert not operator_certificate_path(tmp_path).exists()


def test_happy_path_first_install_without_preexisting_certificate(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    repo_path = _empty_repo(tmp_path)
    settings = Settings(runtime_family="autodl_primary")

    before = ExecutorRegistry({"local_cpu": provider}, build_certificate_store(repo_path=repo_path, data_root=tmp_path))
    assert before.certified_capability(definition, None, "local_cpu") is None

    authority = _authority(definition, provider, tmp_path)
    assert authority.technical_capability_present is True
    assert before.certified_capability(definition, None, "local_cpu") is None

    evidence = _evidence(provider, definition, settings)
    result = install_certificate(
        data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority
    )
    assert result.status == "created"

    rebuilt = ExecutorRegistry({"local_cpu": provider}, build_certificate_store(repo_path=repo_path, data_root=tmp_path))
    assert rebuilt.certified_capability(definition, None, "local_cpu") is not None


def test_idempotency_and_duplicate_semantics(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    repo_path = _empty_repo(tmp_path)
    settings = Settings(runtime_family="autodl_primary")
    authority = _authority(definition, provider, tmp_path)
    evidence = _evidence(provider, definition, settings)

    first = install_certificate(data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority)
    assert first.status == "created"
    second = install_certificate(data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority)
    assert second.status == "already_installed"

    # Conflicting operator metadata for the same key must fail closed.
    stored = load_operator_certificates(tmp_path)
    conflicting = replace(stored[0], evidence_ref="different-evidence")
    operator_path = operator_certificate_path(tmp_path)
    operator_path.write_text(json.dumps({"certificates": [asdict(conflicting)]}), encoding="utf-8")
    with pytest.raises(PlatformError):
        install_certificate(data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority)


def test_repo_default_already_certified_and_cross_duplicate(tmp_path: Path) -> None:
    # Use a repo that already certifies the exact key.
    from app.remote_execution.runtime import ExecutionCertificate

    certificate = ExecutionCertificate(
        plugin_id="dummy", plugin_version="1.0", model_release_id=None,
        executor="local_cpu", device_type="cpu", precision="float32",
        runtime_ref=_runtime_ref(), evidence_ref="repo-default",
    )
    repo_path = tmp_path / "repo.json"
    repo_path.write_text(json.dumps({"certificates": [asdict(certificate)]}), encoding="utf-8")

    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    settings = Settings(runtime_family="autodl_primary")
    authority = _authority(definition, provider, tmp_path)
    evidence = _evidence(provider, definition, settings)

    result = install_certificate(data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority)
    assert result.status == "already_certified"
    assert load_operator_certificates(tmp_path) == []

    # A cross repo/operator duplicate exact key must fail closed at merge.
    operator_path = operator_certificate_path(tmp_path)
    operator_path.write_text(json.dumps({"certificates": [asdict(certificate)]}), encoding="utf-8")
    with pytest.raises(PlatformError):
        build_certificate_store(repo_path=repo_path, data_root=tmp_path)


def test_ineligible_and_vacuous_evidence_cannot_install(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    authority = _authority(definition, provider, tmp_path)
    base = _evidence(provider, definition, Settings(runtime_family="autodl_primary"))

    deferred = replace(base, qualification_type="gpu_deferred", results=(QualificationResult("gpu", False, "GPU_QUALIFICATION_DEFERRED"),), passed=False)
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=deferred, authority=authority)

    unknown = replace(base, qualification_type="unknown_v9")
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=unknown, authority=authority)

    vacuous = replace(base, results=(), passed=True)
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=vacuous, authority=authority)


def test_six_repo_certificates_preserved() -> None:
    certificates = load_execution_certificates(_REPO_PATH)
    assert len(certificates) == 6
    store = ExecutionCertificateStore(certificates)
    refs = {c.runtime_ref for c in certificates}
    assert "local:autodl_primary:cpu:a1237f8faae7" in refs
    assert "local:autodl_primary:gpu:7b958347b5af" in refs


def test_restart_semantics_in_memory_registry_unchanged(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    repo_path = _empty_repo(tmp_path)
    settings = Settings(runtime_family="autodl_primary")
    authority = _authority(definition, provider, tmp_path)
    evidence = _evidence(provider, definition, settings)

    running = ExecutorRegistry({"local_cpu": provider}, build_certificate_store(repo_path=repo_path, data_root=tmp_path))
    install_certificate(data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority)
    # The running process does NOT see it (no hot reload).
    assert running.certified_capability(definition, None, "local_cpu") is None
    fresh = ExecutorRegistry({"local_cpu": provider}, build_certificate_store(repo_path=repo_path, data_root=tmp_path))
    assert fresh.certified_capability(definition, None, "local_cpu") is not None


def test_live_authority_rejects_miswired_provider(tmp_path: Path) -> None:
    definition = _definition()

    # Case A: registry key/name = local_cpu but descriptor.executor = local_gpu.
    from dataclasses import replace as dc_replace

    bad = _Provider(runtime_ref=_runtime_ref())
    original = bad.runtime_descriptor
    bad.runtime_descriptor = lambda: dc_replace(original(), executor="local_gpu")
    with pytest.raises(PlatformError):
        _authority(definition, bad, tmp_path)

    # Case B: registry key = local_cpu but provider.name = local_gpu.
    wrong_name = _Provider(runtime_ref=_runtime_ref(), name="local_gpu")
    with pytest.raises(PlatformError):
        _authority(definition, wrong_name, tmp_path)

    # Case C: exact identity preserved.
    assert _authority(definition, _Provider(runtime_ref=_runtime_ref()), tmp_path).provider_present is True


def test_release_bound_manifest_sha_must_freeze_and_match(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    authority = _authority(definition, provider, tmp_path)
    base = _evidence(provider, definition, Settings(runtime_family="autodl_primary"))

    release_authority = replace(authority, model_release_id="golden", asset_manifest_sha256="c" * 64)
    matching = replace(base, model_release_id="golden", asset_manifest_sha256="c" * 64, evidence_sha256="")
    validate_evidence_for_install(evidence=matching, authority=release_authority)

    stale = replace(base, model_release_id="golden", asset_manifest_sha256="b" * 64, evidence_sha256="")
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=stale, authority=release_authority)


def test_release_less_evidence_has_no_manifest_sha(tmp_path: Path) -> None:
    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    authority = _authority(definition, provider, tmp_path)
    evidence = _evidence(provider, definition, Settings(runtime_family="autodl_primary"))
    assert evidence.asset_manifest_sha256 is None
    assert authority.asset_manifest_sha256 is None
    validate_evidence_for_install(evidence=evidence, authority=authority)


def test_operator_path_under_data_root_and_no_secrets(tmp_path: Path) -> None:
    path = operator_certificate_path(tmp_path)
    assert path == tmp_path / "runtime_certificates.json"
    assert path.parent == tmp_path

    definition = _definition()
    provider = _Provider(runtime_ref=_runtime_ref())
    repo_path = _empty_repo(tmp_path)
    settings = Settings(runtime_family="autodl_primary")
    authority = _authority(definition, provider, tmp_path)
    evidence = _evidence(provider, definition, settings)
    install_certificate(data_root=tmp_path, repo_certificate_path=repo_path, evidence=evidence, authority=authority)
    stored = json.loads(path.read_text(encoding="utf-8"))["certificates"]
    assert set(stored[0]) == {
        "plugin_id", "plugin_version", "model_release_id", "executor",
        "device_type", "precision", "runtime_ref", "evidence_ref",
    }


# ---------------------------------------------------------------------------
# B1: local_gpu_cuda_v1 evidence installs to an exact (never generic) certificate
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


def _gpu_definition() -> PipelineDefinition:
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
        model_release_required=False,
    )


def _gpu_evidence(provider, definition, settings) -> QualificationEvidence:
    from app.runtime_qualification.qualification import (
        LocalGpuQualificationRunner,
        LocalGpuTargetProbe,
    )

    probe = LocalGpuTargetProbe(
        settings=settings,
        definition=definition,
        provider=provider,
        model_release_store=_ReleaseStore(),
        material_probe=lambda: dict(_GPU_MATERIAL),
        now=_CLOCK,
    )
    target = QualificationTarget(
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        model_release_id=None,
        executor="local_gpu",
        runtime_ref=provider.runtime_ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )
    return run_qualification(
        target=target, runner=LocalGpuQualificationRunner(target_probe=probe), now=_CLOCK
    )


def test_install_accepts_local_gpu_cuda_v1_as_exact_certificate(tmp_path: Path) -> None:
    from app.runtime_qualification.identity import (
        BHQ3_GPU_V1,
        derive_generation_for_scheme,
        derive_local_runtime_ref,
    )

    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=_GPU_MATERIAL)
    gpu_ref = derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation)
    assert gpu_ref == "local:autodl_primary:gpu:7b958347b5af"
    definition = _gpu_definition()
    provider = _Provider(runtime_ref=gpu_ref, name="local_gpu", device_type="cuda", precision="float16")
    authority = resolve_live_authority(
        registry=_Pipelines(definition),
        model_release_store=_ReleaseStore(),
        executor_registry=_ExecRegistry({"local_gpu": provider}),
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        executor="local_gpu",
        requested_model_release_id=None,
        data_root=tmp_path,
    )
    assert authority.technical_capability_present is True
    evidence = _gpu_evidence(provider, definition, Settings(runtime_family="autodl_primary"))

    certificate = validate_evidence_for_install(evidence=evidence, authority=authority)
    assert certificate.executor == "local_gpu"
    assert certificate.device_type == "cuda"
    assert certificate.precision == "float16"
    assert certificate.runtime_ref == gpu_ref
    assert certificate.model_release_id is None
    assert len(certificate.key()) == 7

    result = install_certificate(
        data_root=tmp_path,
        repo_certificate_path=_empty_repo(tmp_path),
        evidence=evidence,
        authority=authority,
    )
    assert result.status == "created"
    stored = load_operator_certificates(tmp_path)
    assert [c.key() for c in stored] == [certificate.key()]
