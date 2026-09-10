"""TASK D2 — ExecutorRegistry: provider dispatch + certificate-gated runnable set."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
    RemoteGpuExecutorProvider,
    RuntimeDescriptor,
)

PLUGIN_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
RUNTIME_COMMIT = "5bb5be4b04d04a071bc9d8f4f61172595ecee037"
RUNTIME_REF = f"remote:autodl_primary:{RUNTIME_COMMIT}"
OTHER_REF = f"remote:autodl_primary:{'0' * 40}"


def _definition() -> PipelineDefinition:
    return PipelineDefinition(
        id=PLUGIN_ID,
        name="ZoomSpec",
        version="1.0.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=("remote_gpu",),
        recommended_executor="remote_gpu",
        technical_execution_capabilities=(
            ExecutionCapability("remote_gpu", "cuda", "float16"),
        ),
    )


def _certificate(runtime_ref: str = RUNTIME_REF) -> ExecutionCertificate:
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID,
        plugin_version="1.0.0",
        model_release_id="golden",
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=runtime_ref,
        evidence_ref="m9.1-live-gate",
    )


class FakeProvider:
    def __init__(self, name: str, runtime_ref: str) -> None:
        self.name = name
        self._runtime_ref = runtime_ref
        self.launches: list[str] = []

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(self.name, "cuda", 0, "float16")

    def availability(self, definition, model_release, recording):
        return ExecutorAvailabilityRead(
            executor=self.name, available=True, remote_profile=None, recommended=True
        )

    def launch(self, run_id: str, *, coordinator_token: str | None) -> int:
        self.launches.append(run_id)
        return 1


def _registry(*providers: FakeProvider, certs=None) -> ExecutorRegistry:
    store = ExecutionCertificateStore(
        certs if certs is not None else [_certificate()]
    )
    return ExecutorRegistry({p.name: p for p in providers}, store)


def test_dispatch_by_provider_not_literal():
    provider = FakeProvider("remote_gpu", RUNTIME_REF)
    registry = _registry(provider)
    assert registry.provider("remote_gpu") is provider
    assert registry.provider("remote_gpu").launch("run_x", coordinator_token=None) == 1
    assert provider.launches == ["run_x"]


def test_unknown_provider_raises_capability_unavailable():
    registry = _registry(FakeProvider("remote_gpu", RUNTIME_REF))
    with pytest.raises(PlatformError) as exc:
        registry.provider("local_cpu")
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"


def test_technical_capabilities_passthrough():
    registry = _registry(FakeProvider("remote_gpu", RUNTIME_REF))
    assert registry.technical_capabilities(_definition()) == [
        ExecutionCapability("remote_gpu", "cuda", "float16")
    ]


def test_certified_capabilities_require_matching_runtime_ref():
    registry = _registry(FakeProvider("remote_gpu", RUNTIME_REF))
    definition = _definition()
    assert registry.certified_capabilities(definition, "golden", RUNTIME_REF) == [
        ExecutionCapability("remote_gpu", "cuda", "float16")
    ]
    assert registry.certified_capabilities(definition, "golden", OTHER_REF) == []
    assert registry.certified_capabilities(definition, "tuned", RUNTIME_REF) == []


def test_certified_executors_gated_by_certificate():
    provider = FakeProvider("remote_gpu", RUNTIME_REF)
    registry = _registry(provider)
    assert set(registry.certified_executors(_definition(), "golden")) == {"remote_gpu"}
    assert registry.certified_executors(_definition(), "tuned") == {}


def test_availability_requires_a_certificate():
    provider = FakeProvider("remote_gpu", RUNTIME_REF)
    registry = _registry(provider, certs=[])
    availability = registry.availability(
        _definition(),
        SimpleNamespace(release=SimpleNamespace(model_release_id="golden")),
        SimpleNamespace(label_space="spacenet_14"),
    )
    assert availability.available is False
    assert availability.reason_code == "EXECUTION_NOT_CERTIFIED"


def test_remote_provider_runtime_ref_includes_profile_and_commit():
    profile = SimpleNamespace(name="autodl_primary")
    provider = RemoteGpuExecutorProvider(
        profile=profile, probe=object(), launcher=object(),
        required_runtime_commit=RUNTIME_COMMIT,
    )
    assert provider.runtime_ref == RUNTIME_REF
    descriptor = provider.runtime_descriptor()
    assert descriptor.executor == "remote_gpu"
    assert descriptor.device_type == "cuda"
    assert descriptor.precision == "float16"
    # environment_ref (profile) is private; public projection exposes only the label
    assert descriptor.environment_ref == "autodl_primary"
    assert descriptor.public_projection() == {
        "executor": "remote_gpu",
        "device": "cuda:0",
        "environment": "autodl_primary",
    }
