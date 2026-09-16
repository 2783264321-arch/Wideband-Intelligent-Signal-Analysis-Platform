"""Pragmatic Standard Mode: built-in local CPU provider for release-less CPU pipelines.

These tests pin the narrow allowance only: the built-in STFT Energy Detector path
is runnable without a qualification certificate, while the strict/explicit local_cpu
provider still requires an exact certificate.
"""
import dataclasses
from types import SimpleNamespace

from app.analysis.local_executor import (
    STANDARD_LOCAL_CPU_RUNTIME_REF,
    build_deployment_local_providers,
    build_local_providers,
)
from app.execution_selection.resolver import collect_candidates
from app.pipelines.stft_energy.pipeline import STFTEnergyDetectorPipeline
from app.remote_execution.runtime import ExecutionCertificateStore, ExecutorRegistry


def _definition():
    return STFTEnergyDetectorPipeline().definition


def _registry(provider):
    return ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore([]))


def test_default_settings_use_builtin_standard_provider(settings):
    # Strict-only builder still omits local_cpu when nothing is configured.
    assert "local_cpu" not in build_local_providers(settings)
    providers = build_deployment_local_providers(settings)
    assert "local_cpu" in providers
    provider = providers["local_cpu"]
    assert getattr(provider, "standard_mode", False) is True
    assert provider.runtime_ref == STANDARD_LOCAL_CPU_RUNTIME_REF


def test_explicit_local_cpu_takes_precedence(settings, tmp_path):
    settings.local_cpu_python_path = tmp_path / "python.exe"
    settings.local_cpu_runtime_ref = "local:test:cpu:0123456789ab"
    provider = build_deployment_local_providers(settings)["local_cpu"]
    assert getattr(provider, "standard_mode", False) is False
    assert provider.runtime_ref == "local:test:cpu:0123456789ab"


def test_standard_cpu_is_runnable_without_certificate(settings):
    provider = build_deployment_local_providers(settings)["local_cpu"]
    registry = _registry(provider)
    definition = _definition()
    assert registry.certified_capability(definition, None, "local_cpu") is not None
    recording = SimpleNamespace(label_space="spacenet_14")
    availability = registry.availability_for(definition, None, recording, "local_cpu")
    assert availability.available is True
    candidates = collect_candidates(
        definition=definition, model_release=None, probe_recording=recording,
        executor_registry=registry,
    )
    cpu = next(candidate for candidate in candidates if candidate.executor == "local_cpu")
    assert cpu.technical and cpu.configured and cpu.certified and cpu.available


def test_incompatible_input_still_fails_closed(settings):
    provider = build_deployment_local_providers(settings)["local_cpu"]
    registry = _registry(provider)
    definition = dataclasses.replace(_definition(), input_compatibility=("spacenet_14",))
    recording = SimpleNamespace(label_space="other_14")
    availability = registry.availability_for(definition, None, recording, "local_cpu")
    assert availability.available is False
    assert availability.reason_code == "INPUT_INCOMPATIBLE"


def test_run_dispatch_uses_local_inference_worker_subprocess(settings, monkeypatch):
    provider = build_deployment_local_providers(settings)["local_cpu"]
    captured: dict = {}

    class FakeProc:
        pid = 4321

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr("app.analysis.local_executor.subprocess.Popen", fake_popen)
    pid = provider.launch("run_x", coordinator_token=None)
    assert pid == 4321
    assert captured["args"][1:] == ["-m", "app.analysis.local_inference_worker", "run_x"]


def test_strict_explicit_provider_still_requires_certificate(settings, tmp_path):
    settings.local_cpu_python_path = tmp_path / "python.exe"
    settings.local_cpu_runtime_ref = "local:test:cpu:0123456789ab"
    provider = build_deployment_local_providers(settings)["local_cpu"]
    registry = _registry(provider)
    assert registry.certified_capability(_definition(), None, "local_cpu") is None
