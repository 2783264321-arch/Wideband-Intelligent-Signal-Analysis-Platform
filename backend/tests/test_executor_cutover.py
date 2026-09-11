"""TASK D2C — certified executor registry cutover semantics."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.router import _pipeline_read_model
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.pipelines.dummy import DummyPipeline
from app.pipelines.stft_energy.pipeline import STFTEnergyDetectorPipeline
from app.remote_execution.model_release import ModelReleaseStore, load_model_release_defaults
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.runtime import (
    ExecutorRegistry,
    load_execution_certificates,
)

from executor_fixtures import FakeProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
CERT_PATH = REPO_ROOT / "backend" / "app" / "pipelines" / "execution_certificates.json"
PLUGINS_ROOT = REPO_ROOT / "backend" / "app" / "pipelines"
LOCAL_RUNTIME_REF = "local:autodl_primary:cpu:a1237f8faae7"
REMOTE_RUNTIME_REF = "remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037"


def _store() -> ModelReleaseStore:
    return ModelReleaseStore(
        PLUGINS_ROOT, load_model_release_defaults(PLUGINS_ROOT / "model_release_defaults.json")
    )


def _certificates():
    return load_execution_certificates(CERT_PATH)


def _full_registry() -> ExecutorRegistry:
    from app.remote_execution.runtime import ExecutionCertificateStore

    providers = {
        "local_cpu": FakeProvider("local_cpu", runtime_ref=LOCAL_RUNTIME_REF),
        "remote_gpu": FakeProvider("remote_gpu", runtime_ref=REMOTE_RUNTIME_REF),
    }
    return ExecutorRegistry(providers, ExecutionCertificateStore(_certificates()))


# ---------------------------------------------------------------------------
# A+ deployment-qualified projection
# ---------------------------------------------------------------------------


def test_projection_no_provider_is_empty_but_pipeline_still_listed():
    from app.remote_execution.runtime import ExecutionCertificateStore

    registry = ExecutorRegistry({}, ExecutionCertificateStore(_certificates()))
    for definition in (DummyPipeline().definition, STFTEnergyDetectorPipeline().definition):
        model = _pipeline_read_model(definition, registry, _store())
        assert model.executors_supported == []
        assert model.recommended_executor is None


def test_projection_with_certified_providers():
    registry = _full_registry()
    store = _store()
    dummy = _pipeline_read_model(DummyPipeline().definition, registry, store)
    assert dummy.executors_supported == ["local_cpu"]
    assert dummy.recommended_executor is None  # no recommended_execution declared

    stft = _pipeline_read_model(STFTEnergyDetectorPipeline().definition, registry, store)
    assert stft.executors_supported == ["local_cpu"]

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION

    zoom = _pipeline_read_model(ZOOMSPEC_FROZEN_DEFINITION, registry, store)
    assert zoom.executors_supported == ["remote_gpu"]
    assert zoom.recommended_executor == "remote_gpu"


def test_technical_capability_alone_is_not_runnable():
    from app.remote_execution.runtime import ExecutionCertificateStore

    # A local provider whose runtime generation has no certificate.
    registry = ExecutorRegistry(
        {"local_cpu": FakeProvider("local_cpu", runtime_ref="local:uncertified")},
        ExecutionCertificateStore(_certificates()),
    )
    model = _pipeline_read_model(DummyPipeline().definition, registry, _store())
    assert DummyPipeline().definition.technical_execution_capabilities  # declared
    assert model.executors_supported == []
    assert model.recommended_executor is None


# ---------------------------------------------------------------------------
# Exact requested-executor dispatch (no substitution)
# ---------------------------------------------------------------------------


def test_exact_executor_availability_does_not_substitute():
    registry = _full_registry()
    dummy = DummyPipeline().definition
    recording = SimpleNamespace(label_space="spacenet_14")
    # remote_gpu is certified for ZoomSpec but NOT technically supported by dummy;
    # the registry must not fall back to dummy's certified local_cpu.
    result = registry.availability_for(dummy, None, recording, "remote_gpu")
    assert result.available is False
    assert result.reason_code == "EXECUTION_NOT_CERTIFIED"
    assert result.executor == "remote_gpu"


def test_exact_executor_availability_available_for_certified_local():
    registry = _full_registry()
    result = registry.availability_for(
        DummyPipeline().definition, None, SimpleNamespace(label_space="spacenet_14"), "local_cpu"
    )
    assert result.available is True
    assert result.executor == "local_cpu"


def test_unknown_executor_is_capability_unavailable():
    registry = _full_registry()
    result = registry.availability_for(
        DummyPipeline().definition, None, SimpleNamespace(label_space="spacenet_14"), "local_gpu"
    )
    assert result.available is False
    assert result.reason_code == "EXECUTION_CAPABILITY_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Release resolution semantics
# ---------------------------------------------------------------------------


def _service(store) -> AnalysisService:
    return AnalysisService(None, None, None, model_release_store=store)


def test_release_less_plugin_rejects_explicit_release():
    with pytest.raises(PlatformError) as exc:
        _service(_store())._resolve_release(DummyPipeline().definition, "golden")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"


def test_release_less_plugin_resolves_none():
    assert _service(_store())._resolve_release(DummyPipeline().definition, None) is None


def test_release_bound_plugin_requires_a_model_release():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION

    resolved = _service(_store())._resolve_release(ZOOMSPEC_FROZEN_DEFINITION, None)
    assert resolved.release.model_release_id == "golden"


# ---------------------------------------------------------------------------
# D2C FIX ROUND 1 — descriptor-bound certification + fail-closed default release
# ---------------------------------------------------------------------------


def _definition_with(capability: ExecutionCapability, *, plugin_id: str = "p", model_release_required: bool = False) -> PipelineDefinition:
    return PipelineDefinition(
        id=plugin_id,
        name="P",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=(capability.executor,),
        recommended_executor=capability.executor,
        model_release_required=model_release_required,
        technical_execution_capabilities=(capability,),
    )


def test_certificate_must_match_provider_actual_descriptor():
    from app.remote_execution.runtime import ExecutionCertificate, ExecutionCertificateStore

    definition = _definition_with(ExecutionCapability("local_cpu", "cuda", "float16"))
    certificate = ExecutionCertificate(
        plugin_id="p", plugin_version="1.0", model_release_id=None,
        executor="local_cpu", device_type="cuda", precision="float16",
        runtime_ref="fake:local_cpu", evidence_ref="x",
    )
    provider = FakeProvider("local_cpu", runtime_ref="fake:local_cpu")  # actual descriptor = cpu/float32
    assert provider.runtime_descriptor().device_type == "cpu"
    registry = ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore([certificate]))

    assert registry.certified_capability(definition, None, "local_cpu") is None
    result = registry.availability_for(
        definition, None, SimpleNamespace(label_space="spacenet_14"), "local_cpu"
    )
    assert result.available is False
    assert result.reason_code == "EXECUTION_NOT_CERTIFIED"


def test_matching_descriptor_and_certificate_remains_certified():
    from app.remote_execution.runtime import ExecutionCertificate, ExecutionCertificateStore

    definition = _definition_with(ExecutionCapability("local_cpu", "cpu", "float32"))
    certificate = ExecutionCertificate(
        plugin_id="p", plugin_version="1.0", model_release_id=None,
        executor="local_cpu", device_type="cpu", precision="float32",
        runtime_ref="fake:local_cpu", evidence_ref="x",
    )
    provider = FakeProvider("local_cpu", runtime_ref="fake:local_cpu")
    registry = ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore([certificate]))

    assert registry.certified_capability(definition, None, "local_cpu") is not None
    result = registry.availability_for(
        definition, None, SimpleNamespace(label_space="spacenet_14"), "local_cpu"
    )
    assert result.available is True


def test_unresolvable_default_release_fails_closed_even_with_none_certificate():
    from app.remote_execution.runtime import ExecutionCertificate, ExecutionCertificateStore

    definition = _definition_with(
        ExecutionCapability("local_cpu", "cpu", "float32"),
        plugin_id="required_plugin",
        model_release_required=True,
    )
    certificate = ExecutionCertificate(
        plugin_id="required_plugin", plugin_version="1.0", model_release_id=None,
        executor="local_cpu", device_type="cpu", precision="float32",
        runtime_ref="fake:local_cpu", evidence_ref="x",
    )
    registry = ExecutorRegistry(
        {"local_cpu": FakeProvider("local_cpu", runtime_ref="fake:local_cpu")},
        ExecutionCertificateStore([certificate]),
    )
    # The real store cannot resolve a default for the unregistered plugin.
    model = _pipeline_read_model(definition, registry, _store())
    assert model.executors_supported == []
    assert model.recommended_executor is None


def test_runtime_descriptor_does_not_change_legacy_request_hash():
    ids_a = iter(["rid", "bid", "ik"])
    ids_b = iter(["rid", "bid", "ik"])
    base = dict(
        local_run_id="run_x",
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version="1.0.0",
        required_remote_runtime_commit="a" * 40,
        orchestrator_commit="a" * 40,
        asset_manifest_sha256="b" * 64,
        remote_profile="autodl_primary",
    )
    without = freeze_request_provenance(**base, id_factory=lambda: next(ids_a))
    with_descriptor = freeze_request_provenance(**base, id_factory=lambda: next(ids_b))
    with_descriptor["runtime_descriptor"] = {"executor": "remote_gpu"}
    assert without["request_sha256"] == with_descriptor["request_sha256"]
    assert build_batch(with_descriptor).request_sha256 == without["request_sha256"]
