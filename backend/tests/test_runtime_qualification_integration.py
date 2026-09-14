"""Task 7: A1/A2 integration guards for the A3 runtime qualification path.

No production code expected. GPU REQUIRED: NO.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

from executor_fixtures import FakeProvider

from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.runtime import (
    ExecutionCertificateStore,
    ExecutorRegistry,
    load_execution_certificates,
)
from app.runtime_qualification.identity import (
    LEGACY_OPAQUE,
    LOCAL_CPU_V1,
    collect_identity_material,
    derive_generation_for_scheme,
    derive_local_runtime_ref,
    resolve_identity_scheme,
    validate_runtime_ref_against_material,
)
from app.runtime_qualification.install import (
    build_certificate_store,
    install_certificate,
    resolve_live_authority,
)
from app.runtime_qualification.qualification import (
    LocalCpuQualificationRunner,
    LocalCpuTargetProbe,
    QualificationTarget,
    run_qualification,
)

_CLOCK = lambda: datetime(2026, 9, 14, tzinfo=timezone.utc)
_REPO_PATH = Path(__file__).resolve().parents[1] / "app" / "pipelines" / "execution_certificates.json"
_LEGACY_CPU_REF = "local:autodl_primary:cpu:a1237f8faae7"


class _Pipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="dummy",
            name="Dummy",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            executors_supported=("local_cpu",),
            recommended_executor="local_cpu",
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        )

    def run(self, recording, parameters, workspace):  # pragma: no cover - never executed
        raise AssertionError("must not execute")


class _ReleaseStore:
    def resolve(self, plugin_id, plugin_version, requested):
        raise PlatformError("MODEL_RELEASE_NOT_FOUND", "no releases")


class _IntegProvider(FakeProvider):
    def probe(self):
        return (True, None)


def _material_and_ref() -> tuple[dict, str]:
    material = collect_identity_material(Path(sys.executable))
    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=material)
    return material, derive_local_runtime_ref(family="autodl_primary", kind="cpu", generation=generation)


def _provider(runtime_ref: str) -> _IntegProvider:
    return _IntegProvider("local_cpu", device_type="cpu", precision="float32", runtime_ref=runtime_ref)


def _pipelines() -> PipelineRegistry:
    return PipelineRegistry([_Pipeline()])


def _add_recording(client, recording_id: str = "rec_x") -> None:
    with client.app.state.database.session_factory() as session:
        session.add(
            RecordingModel(
                id=recording_id, name="0", data_path="recordings/0/raw.iq",
                data_format="complex64_le", sample_rate_hz=1e6, center_frequency_hz=0.0,
                frequency_low_hz=-5e5, frequency_high_hz=5e5, num_samples=1000, duration_s=0.001,
                dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
                source_data_sha256="1" * 64,
            )
        )
        session.commit()


def _install_certificate(settings: Settings, definition: PipelineDefinition, provider) -> None:
    material, ref = _material_and_ref()
    probe = LocalCpuTargetProbe(
        settings=Settings(runtime_family="autodl_primary"),
        definition=definition,
        provider=provider,
        model_release_store=_ReleaseStore(),
        material_probe=lambda: dict(material),
        now=_CLOCK,
    )
    target = QualificationTarget(
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        model_release_id=None,
        executor="local_cpu",
        runtime_ref=ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )
    evidence = run_qualification(
        target=target, runner=LocalCpuQualificationRunner(target_probe=probe), now=_CLOCK
    )
    authority = resolve_live_authority(
        registry=_pipelines(),
        model_release_store=_ReleaseStore(),
        executor_registry=ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore([])),
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        executor="local_cpu",
        requested_model_release_id=None,
        data_root=settings.data_root,
    )
    install_certificate(
        data_root=settings.data_root,
        repo_certificate_path=_REPO_PATH,
        evidence=evidence,
        authority=authority,
    )


def _selection(client):
    return client.get(
        "/api/executor-selection", params={"pipeline_id": "dummy", "recording_id": "rec_x"}
    )


def test_a2_sees_installed_certificate_after_rebuild(client, settings) -> None:
    definition = _Pipeline().definition
    _, ref = _material_and_ref()
    provider = _provider(ref)
    _add_recording(client)
    client.app.state.pipeline_registry = _pipelines()
    client.app.state.model_release_store = _ReleaseStore()

    # Before install: exact technical capability present, certification absent.
    before_registry = ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore([]))
    assert before_registry.certified_capability(definition, None, "local_cpu") is None
    client.app.state.executor_registry = before_registry
    before = _selection(client).json()
    before_candidate = {c["executor"]: c for c in before["candidates"]}["local_cpu"]
    assert before_candidate["technical"] is True
    assert before_candidate["certified"] is False

    _install_certificate(settings, definition, provider)

    # After install + registry rebuild: A2 sees the certificate.
    client.app.state.executor_registry = ExecutorRegistry(
        {"local_cpu": provider},
        build_certificate_store(repo_path=_REPO_PATH, data_root=settings.data_root),
    )
    after = _selection(client).json()
    after_candidate = {c["executor"]: c for c in after["candidates"]}["local_cpu"]
    assert after_candidate["certified"] is True
    # A2 policy inputs (technical/configured) are untouched; only certification flips.
    assert after_candidate["technical"] == before_candidate["technical"]
    assert after_candidate["configured"] == before_candidate["configured"]
    assert after["resolved_executor"] == "local_cpu"


def test_six_repo_certificates_remain_loadable() -> None:
    certificates = load_execution_certificates(_REPO_PATH)
    assert len(certificates) == 6
    ExecutionCertificateStore(certificates)
    refs = {certificate.runtime_ref for certificate in certificates}
    assert _LEGACY_CPU_REF in refs
    assert "local:autodl_primary:gpu:7b958347b5af" in refs


def test_legacy_cpu_ref_is_provenance_based_and_not_rederived() -> None:
    certificates = load_execution_certificates(_REPO_PATH)
    repo_refs = frozenset(certificate.runtime_ref for certificate in certificates)
    assert resolve_identity_scheme(
        executor="local_cpu",
        runtime_ref=_LEGACY_CPU_REF,
        qualification_context=None,
        repo_default_runtime_refs=repo_refs,
        material_available=False,
    ) == LEGACY_OPAQUE
    # A same-shaped ref without provenance is not legacy.
    assert resolve_identity_scheme(
        executor="local_cpu",
        runtime_ref="local:autodl_primary:cpu:ffffffffffff",
        qualification_context=None,
        repo_default_runtime_refs=repo_refs,
        material_available=False,
    ) != LEGACY_OPAQUE
    # The legacy ref cannot be reproduced by local_cpu_v1 (never re-derived).
    material, _ = _material_and_ref()
    with pytest.raises(PlatformError):
        validate_runtime_ref_against_material(
            runtime_ref=_LEGACY_CPU_REF,
            family="autodl_primary",
            kind="cpu",
            scheme=LOCAL_CPU_V1,
            material=material,
        )


def test_a1_frozen_authority_preserved_and_rotation_invalidates(settings) -> None:
    definition = _Pipeline().definition
    _, ref = _material_and_ref()
    provider = _provider(ref)
    _install_certificate(settings, definition, provider)
    registry = ExecutorRegistry(
        {"local_cpu": provider},
        build_certificate_store(repo_path=_REPO_PATH, data_root=settings.data_root),
    )
    frozen = provider.runtime_descriptor()
    assert registry.validate_frozen_execution_authority(definition, None, "local_cpu", frozen) is provider

    # Descriptor drift fails closed with the A1 code.
    rotated_descriptor = type(frozen)(
        executor=frozen.executor, device_type=frozen.device_type, device_index=frozen.device_index,
        precision=frozen.precision, environment_ref="/other/python", environment_label=frozen.environment_label,
    )
    with pytest.raises(PlatformError) as exc:
        registry.validate_frozen_execution_authority(definition, None, "local_cpu", rotated_descriptor)
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"

    # Runtime rotation (new runtime_ref) invalidates the old certificate.
    rotated_provider = _provider("local:autodl_primary:cpu:0123456789ab")
    rotated_registry = ExecutorRegistry(
        {"local_cpu": rotated_provider},
        build_certificate_store(repo_path=_REPO_PATH, data_root=settings.data_root),
    )
    assert rotated_registry.certified_capability(definition, None, "local_cpu") is None
    with pytest.raises(PlatformError) as exc2:
        rotated_registry.validate_frozen_execution_authority(
            definition, None, "local_cpu", rotated_provider.runtime_descriptor()
        )
    assert exc2.value.code == "EXECUTION_NOT_CERTIFIED"


def test_certificate_and_provider_disappearance_fail_closed(settings) -> None:
    definition = _Pipeline().definition
    _, ref = _material_and_ref()
    provider = _provider(ref)
    _install_certificate(settings, definition, provider)

    # Provider disappears.
    no_provider = ExecutorRegistry(
        {}, build_certificate_store(repo_path=_REPO_PATH, data_root=settings.data_root)
    )
    with pytest.raises(PlatformError) as exc:
        no_provider.validate_frozen_execution_authority(
            definition, None, "local_cpu", provider.runtime_descriptor()
        )
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"

    # Certificate disappears (fresh data root with no operator store).
    import tempfile

    with tempfile.TemporaryDirectory() as empty_root:
        no_cert = ExecutorRegistry(
            {"local_cpu": provider},
            build_certificate_store(repo_path=_REPO_PATH, data_root=Path(empty_root)),
        )
        assert no_cert.certified_capability(definition, None, "local_cpu") is None
        with pytest.raises(PlatformError) as exc2:
            no_cert.validate_frozen_execution_authority(
                definition, None, "local_cpu", provider.runtime_descriptor()
            )
        assert exc2.value.code == "EXECUTION_NOT_CERTIFIED"


def test_only_install_module_writes_operator_store() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    writers = []
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "runtime_certificates.json" in source or "_write_operator_certificates" in source:
            writers.append(path.name)
    assert writers == ["install.py"]


def test_no_a3_internals_leak_to_http(client, settings) -> None:
    _add_recording(client)
    definition = _Pipeline().definition
    _, ref = _material_and_ref()
    provider = _provider(ref)
    client.app.state.pipeline_registry = _pipelines()
    client.app.state.model_release_store = _ReleaseStore()
    client.app.state.executor_registry = ExecutorRegistry(
        {"local_cpu": provider},
        build_certificate_store(repo_path=_REPO_PATH, data_root=settings.data_root),
    )

    for response in (_selection(client), client.get("/api/pipelines")):
        assert response.status_code == 200
        text = response.text
        for secret in (
            "environment_ref",
            "executable_id",
            "qualification",
            "runtime_certificates",
            "local_cpu_python_path",
            "evidence_sha256",
        ):
            assert secret not in text
