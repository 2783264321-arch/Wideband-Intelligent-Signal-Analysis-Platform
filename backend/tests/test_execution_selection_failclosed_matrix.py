"""A2 fail-closed / portability matrix (verification).

Consolidates the cross-cutting invariants across the Auto policy, resolver, and
integrated services/API into one reviewable matrix. Introduces no production
code.
"""
import ast
import pathlib
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.execution_selection import policy
from app.execution_selection import resolver as resolver_module
from app.execution_selection.resolver import collect_candidates, resolve_auto_execution
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
)

from executor_fixtures import FakeProvider

_DEVICE = {
    "local_cpu": ("cpu", "float32"),
    "local_gpu": ("cuda", "float16"),
    "remote_gpu": ("cuda", "float16"),
}
_OPEN_REASON_CODES = {
    "AUTO_NO_RUNNABLE_EXECUTOR", "AUTO_ONLY_RUNNABLE_EXECUTOR",
    "AUTO_LOCAL_CPU_PREFERRED", "AUTO_LOCAL_GPU_PREFERRED",
    "AUTO_REMOTE_GPU_PREFERRED", "AUTO_UNKNOWN_RECOMMENDED_EXECUTOR",
    "AUTO_UNKNOWN_DETERMINISTIC_RANK",
}


@dataclass(frozen=True)
class _Recording:
    label_space: str | None = "spacenet_14"
    num_samples: int = 1000
    duration_s: float = 0.01
    source_data_sha256: str | None = None


class _Launcher:
    def launch(self, run_id, coordinator_token):
        return 1


class _PathProbe:
    def availability(self, recording, definition, source_data_sha256, model_release=None):
        return ExecutorAvailabilityRead(
            executor="local_cpu", available=False,
            reason_code="EXECUTION_CAPABILITY_UNAVAILABLE",
            reason_message=r"C:\secret\runtime\python.exe /root/private/model.pt",
            remote_profile=None, recommended=False,
        )


def _definition(executors, *, recommended_execution=None, label_space="spacenet_14",
                input_compatibility=()):
    capabilities = tuple(ExecutionCapability(e, *_DEVICE[e]) for e in executors)
    return PipelineDefinition(
        id="matrix", name="Matrix", version="1.0", label_space=label_space,
        recommended_device="GPU", cpu_supported=True, stages=(), inspectable_stages=(),
        task_capability="detection_classification", executors_supported=tuple(executors),
        recommended_executor=recommended_execution or executors[0],
        technical_execution_capabilities=capabilities,
        recommended_execution=recommended_execution,
        input_compatibility=tuple(input_compatibility),
    )


def _provider(executor, *, available=True, reason_code=None, probe=None, launcher=None):
    device_type, precision = _DEVICE[executor]
    return FakeProvider(executor, device_type=device_type, precision=precision,
                        available=available, reason_code=reason_code, probe=probe,
                        launcher=launcher)


def _cert(executor):
    device_type, precision = _DEVICE[executor]
    return ExecutionCertificate(
        plugin_id="matrix", plugin_version="1.0", model_release_id=None,
        executor=executor, device_type=device_type, precision=precision,
        runtime_ref=f"fake:{executor}", evidence_ref="test",
    )


def _registry(providers, certs):
    return ExecutorRegistry(dict(providers), ExecutionCertificateStore(list(certs)))


def _by(candidates):
    return {candidate.executor: candidate for candidate in candidates}


def _resolve(definition, registry, *, recording=None, dataset_item_count=None):
    return resolve_auto_execution(
        definition=definition, model_release=None,
        probe_recording=recording or _Recording(),
        executor_registry=registry, dataset_item_count=dataset_item_count,
    )


def _all_runnable(executors):
    providers = {}
    for executor in executors:
        providers[executor] = _provider(executor, launcher=_Launcher() if executor == "remote_gpu" else None)
    return _registry(providers, [_cert(e) for e in executors])


# --- runnable counts -------------------------------------------------------

def test_zero_runnable_fails_closed():
    registry = _registry({"local_cpu": _provider("local_cpu", available=False,
                                                 reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
                         [_cert("local_cpu")])
    selection = _resolve(_definition(("local_cpu",)), registry)
    assert selection.resolved_executor is None
    assert selection.reason_code == "AUTO_NO_RUNNABLE_EXECUTOR"


def test_one_runnable_only_runnable():
    registry = _all_runnable(("remote_gpu",))
    selection = _resolve(_definition(("remote_gpu",)), registry)
    assert selection.resolved_executor == "remote_gpu"
    assert selection.reason_code == "AUTO_ONLY_RUNNABLE_EXECUTOR"


def test_two_runnable_uses_small_ranking():
    registry = _all_runnable(("local_cpu", "local_gpu"))
    selection = _resolve(_definition(("local_cpu", "local_gpu")), registry)
    assert selection.resolved_executor == "local_cpu"
    assert selection.reason_code == "AUTO_LOCAL_CPU_PREFERRED"


def test_three_runnable_uses_gpu_beneficial_ranking():
    registry = _all_runnable(("local_cpu", "local_gpu", "remote_gpu"))
    selection = _resolve(_definition(("local_cpu", "local_gpu", "remote_gpu")), registry,
                         dataset_item_count=8)
    assert selection.workload_class == "GPU_BENEFICIAL"
    assert selection.resolved_executor == "local_gpu"


# --- exclusion reasons -----------------------------------------------------

def test_technical_but_not_configured_excluded():
    registry = _registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    candidate = _by(collect_candidates(definition=_definition(("local_cpu", "local_gpu")),
                                       model_release=None, probe_recording=_Recording(),
                                       executor_registry=registry))["local_gpu"]
    assert (candidate.technical, candidate.configured, candidate.available) == (True, False, False)


def test_configured_but_uncertified_excluded():
    registry = _registry({"local_cpu": _provider("local_cpu")}, [])
    candidate = _by(collect_candidates(definition=_definition(("local_cpu",)), model_release=None,
                                       probe_recording=_Recording(),
                                       executor_registry=registry))["local_cpu"]
    assert candidate.certified is False
    assert candidate.reason_code == "EXECUTION_NOT_CERTIFIED"


def test_certified_but_unavailable_excluded():
    registry = _registry({"local_cpu": _provider("local_cpu", available=False,
                                                 reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
                         [_cert("local_cpu")])
    candidate = _by(collect_candidates(definition=_definition(("local_cpu",)), model_release=None,
                                       probe_recording=_Recording(),
                                       executor_registry=registry))["local_cpu"]
    assert candidate.certified is True
    assert candidate.available is False
    assert candidate.safe_reason_message == "Executor is currently unavailable."


def test_input_incompatible_excluded():
    registry = _registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    candidate = _by(collect_candidates(definition=_definition(("local_cpu",)), model_release=None,
                                       probe_recording=_Recording(label_space="signal_presence_v1"),
                                       executor_registry=registry))["local_cpu"]
    assert candidate.reason_code == "INPUT_INCOMPATIBLE"


def test_remote_unavailable_excluded():
    registry = _registry({"remote_gpu": _provider("remote_gpu")}, [_cert("remote_gpu")])
    candidate = _by(collect_candidates(definition=_definition(("remote_gpu",)), model_release=None,
                                       probe_recording=_Recording(),
                                       executor_registry=registry))["remote_gpu"]
    assert candidate.reason_code == "REMOTE_EXECUTOR_UNAVAILABLE"


# --- workload / recommendation --------------------------------------------

def test_unknown_workload_recommended_and_fallback():
    registry = _all_runnable(("local_cpu", "local_gpu"))
    # No size info -> UNKNOWN; recommended runnable -> recommends it.
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu", "local_gpu"), recommended_execution="local_gpu"),
        model_release=None, probe_recording=SimpleNamespace(label_space="spacenet_14",
                                                            duration_s=None, num_samples=None,
                                                            source_data_sha256=None),
        executor_registry=registry,
    )
    assert selection.workload_class == "UNKNOWN"
    assert selection.reason_code == "AUTO_UNKNOWN_RECOMMENDED_EXECUTOR"


def test_recommended_unavailable_not_selected():
    registry = _registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu",), recommended_execution="remote_gpu"),
        model_release=None, probe_recording=_Recording(),
        executor_registry=registry,
    )
    assert selection.resolved_executor == "local_cpu"


# --- safe reason projection ------------------------------------------------

def test_safe_reason_projection_has_no_paths():
    registry = _registry({"local_cpu": _provider("local_cpu", probe=_PathProbe())},
                         [_cert("local_cpu")])
    candidate = _by(collect_candidates(definition=_definition(("local_cpu",)), model_release=None,
                                       probe_recording=_Recording(),
                                       executor_registry=registry))["local_cpu"]
    message = candidate.safe_reason_message or ""
    assert r"C:\secret" not in message
    assert "/root/" not in message
    assert policy.public_reason_message(candidate.reason_code) == message


# --- portability -----------------------------------------------------------

@pytest.mark.parametrize("module", [policy, resolver_module])
def test_execution_selection_modules_are_portable(module):
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert imported <= {"dataclasses", "enum", "typing", "app"}
    lowered = source.lower()
    for forbidden in ("cgroup", "nvidia-smi", "bhq3_memory_gate", "/proc", "autodl",
                      "subprocess", "ultralytics", "torch"):
        assert forbidden not in lowered


def test_reason_code_set_is_closed():
    assert policy.AUTO_NO_RUNNABLE_EXECUTOR in _OPEN_REASON_CODES
    for code in _OPEN_REASON_CODES:
        assert policy.public_reason_message(code)
