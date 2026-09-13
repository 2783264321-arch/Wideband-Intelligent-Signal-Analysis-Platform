"""Plan A1 / Task 3 — frozen execution-authority validation seam.

Proves the seam compares the FULL internal RuntimeDescriptor (executor,
device_type, device_index, precision, environment_ref, environment_label) and
requires an exact certificate for that identity, and never runs a live probe.
"""

from __future__ import annotations

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
    RuntimeDescriptor,
)

PLUGIN_ID = "cpn_bandwidth_tier"
VERSION = "1.0.0"
REF_A = "local:autodl_primary:gpu:aaaaaaaaaaaa"
REF_B = "local:autodl_primary:gpu:bbbbbbbbbbbb"


def _definition() -> PipelineDefinition:
    return PipelineDefinition(
        id=PLUGIN_ID,
        name="CPN",
        version=VERSION,
        label_space="cpn_bandwidth_tier_v1",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=("local_gpu",),
        recommended_executor="local_gpu",
        technical_execution_capabilities=(
            ExecutionCapability("local_gpu", "cuda", "float16"),
        ),
    )


def _descriptor(*, index=0, ref="/root/miniconda3/bin/python", label=REF_A) -> RuntimeDescriptor:
    return RuntimeDescriptor(
        "local_gpu", "cuda", index, "float16",
        environment_ref=ref, environment_label=label,
    )


def _certificate(runtime_ref: str = REF_A) -> ExecutionCertificate:
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID,
        plugin_version=VERSION,
        model_release_id="golden",
        executor="local_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=runtime_ref,
        evidence_ref="bhq3",
    )


class SpyProvider:
    def __init__(self, runtime_ref: str, descriptor: RuntimeDescriptor) -> None:
        self.name = "local_gpu"
        self._runtime_ref = runtime_ref
        self._descriptor = descriptor
        self.probe_calls = 0

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    def probe(self):
        self.probe_calls += 1
        return True, None

    def availability(self, definition, model_release, recording):  # pragma: no cover
        raise AssertionError("availability must not be called by the authority seam")

    def launch(self, run_id, *, coordinator_token):  # pragma: no cover
        return 1


def _registry(provider, certs) -> ExecutorRegistry:
    providers = {} if provider is None else {provider.name: provider}
    return ExecutorRegistry(providers, ExecutionCertificateStore(certs))


def test_authority_ok_returns_provider():
    provider = SpyProvider(REF_A, _descriptor())
    registry = _registry(provider, [_certificate(REF_A)])
    result = registry.validate_frozen_execution_authority(
        _definition(), "golden", "local_gpu", _descriptor()
    )
    assert result is provider
    assert provider.probe_calls == 0  # never a live probe


def test_authority_missing_provider_raises_execution_capability_unavailable():
    registry = _registry(None, [_certificate(REF_A)])
    with pytest.raises(PlatformError) as exc:
        registry.validate_frozen_execution_authority(
            _definition(), "golden", "local_gpu", _descriptor()
        )
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"


def test_authority_full_descriptor_mismatch_raises_runtime_descriptor_invalid():
    provider = SpyProvider(REF_A, _descriptor(index=1))
    registry = _registry(provider, [_certificate(REF_A)])
    with pytest.raises(PlatformError) as exc:
        registry.validate_frozen_execution_authority(
            _definition(), "golden", "local_gpu", _descriptor(index=0)
        )
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"


def test_authority_environment_identity_mismatch_raises_runtime_descriptor_invalid():
    # same executor/device/precision; different frozen runtime generation A vs B
    provider = SpyProvider(REF_B, _descriptor(label=REF_B))
    registry = _registry(provider, [_certificate(REF_A), _certificate(REF_B)])
    with pytest.raises(PlatformError) as exc:
        registry.validate_frozen_execution_authority(
            _definition(), "golden", "local_gpu", _descriptor(label=REF_A)
        )
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"


def test_authority_other_generation_certificate_cannot_hijack():
    # provider is generation B and HAS a valid B certificate; frozen is A -> fail closed
    provider = SpyProvider(REF_B, _descriptor(label=REF_B))
    registry = _registry(provider, [_certificate(REF_B)])
    with pytest.raises(PlatformError) as exc:
        registry.validate_frozen_execution_authority(
            _definition(), "golden", "local_gpu", _descriptor(label=REF_A)
        )
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"


def test_authority_missing_certificate_raises_execution_not_certified():
    provider = SpyProvider(REF_A, _descriptor())
    registry = _registry(provider, [_certificate(REF_B)])
    with pytest.raises(PlatformError) as exc:
        registry.validate_frozen_execution_authority(
            _definition(), "golden", "local_gpu", _descriptor()
        )
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_authority_does_not_call_provider_probe_on_failure():
    provider = SpyProvider(REF_A, _descriptor(index=2))
    registry = _registry(provider, [_certificate(REF_A)])
    with pytest.raises(PlatformError):
        registry.validate_frozen_execution_authority(
            _definition(), "golden", "local_gpu", _descriptor(index=0)
        )
    assert provider.probe_calls == 0
