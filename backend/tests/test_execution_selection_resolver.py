import ast
import pathlib
from dataclasses import dataclass

from app.analysis.schema import ExecutorAvailabilityRead
from app.execution_selection import resolver
from app.execution_selection.resolver import (
    ExecutionCandidate,
    ExecutionSelection,
    collect_candidates,
    resolve_auto_execution,
)
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


@dataclass(frozen=True)
class _Recording:
    label_space: str | None
    num_samples: int
    duration_s: float
    source_data_sha256: str | None = None


class _StubLauncher:
    def launch(self, run_id, coordinator_token):
        return 1


class _MaliciousProbe:
    """Returns raw provider detail that must never reach the public projection."""

    def availability(self, recording, definition, source_data_sha256, model_release=None):
        return ExecutorAvailabilityRead(
            executor="local_cpu",
            available=False,
            reason_code="EXECUTION_CAPABILITY_UNAVAILABLE",
            reason_message=r"C:\secret\runtime\python.exe /root/private/model.pt",
            remote_profile=None,
            recommended=False,
        )


def _provider(executor, *, available=True, reason_code=None, probe=None, launcher=None):
    device_type, precision = _DEVICE[executor]
    return FakeProvider(
        executor,
        device_type=device_type,
        precision=precision,
        available=available,
        reason_code=reason_code,
        probe=probe,
        launcher=launcher,
    )


def _cert(executor, *, model_release_id=None):
    device_type, precision = _DEVICE[executor]
    return ExecutionCertificate(
        plugin_id="g_auto",
        plugin_version="1.0",
        model_release_id=model_release_id,
        executor=executor,
        device_type=device_type,
        precision=precision,
        runtime_ref=f"fake:{executor}",
        evidence_ref="test",
    )


def _definition(executors, *, recommended_execution=None, label_space="spacenet_14",
                input_compatibility=()):
    capabilities = tuple(ExecutionCapability(e, *_DEVICE[e]) for e in executors)
    return PipelineDefinition(
        id="g_auto",
        name="G Auto",
        version="1.0",
        label_space=label_space,
        recommended_device="GPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=tuple(executors),
        recommended_executor=recommended_execution or executors[0],
        technical_execution_capabilities=capabilities,
        recommended_execution=recommended_execution,
        input_compatibility=tuple(input_compatibility),
    )


def _real_registry(providers, certificates=()):
    return ExecutorRegistry(dict(providers), ExecutionCertificateStore(list(certificates)))


class _CountingRegistry:
    """Delegates to the REAL ExecutorRegistry and records availability_for calls."""

    def __init__(self, inner):
        self._inner = inner
        self.availability_calls = []

    def providers(self):
        return self._inner.providers()

    def certified_capability(self, definition, model_release_id, executor):
        return self._inner.certified_capability(definition, model_release_id, executor)

    def availability_for(self, definition, model_release, recording, executor):
        self.availability_calls.append(executor)
        return self._inner.availability_for(definition, model_release, recording, executor)


def _recording(**overrides):
    values = dict(label_space="spacenet_14", num_samples=1000, duration_s=0.01)
    values.update(overrides)
    return _Recording(**values)


def _by_executor(candidates):
    return {candidate.executor: candidate for candidate in candidates}


# Case 1 — technical but no provider
def test_candidate_technical_without_provider_is_short_circuited():
    definition = _definition(("local_cpu", "local_gpu"))
    registry = _CountingRegistry(
        _real_registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    )
    candidates = collect_candidates(
        definition=definition, model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    local_gpu = _by_executor(candidates)["local_gpu"]
    assert local_gpu.technical is True
    assert local_gpu.configured is False
    assert local_gpu.certified is False
    assert local_gpu.available is False
    assert local_gpu.reason_code is None
    assert local_gpu.safe_reason_message is None
    assert "local_gpu" not in registry.availability_calls


# Case 2 — provider but no exact certificate (real registry + empty store)
def test_candidate_provider_without_certificate_is_not_certified():
    definition = _definition(("local_cpu",))
    registry = _CountingRegistry(_real_registry({"local_cpu": _provider("local_cpu")}, []))
    candidates = collect_candidates(
        definition=definition, model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    local_cpu = _by_executor(candidates)["local_cpu"]
    assert local_cpu.technical is True
    assert local_cpu.configured is True
    assert local_cpu.certified is False
    assert local_cpu.available is False
    assert local_cpu.reason_code == "EXECUTION_NOT_CERTIFIED"


# Case 3 — certified but live unavailable
def test_candidate_certified_but_unavailable_is_excluded():
    provider = _provider("local_cpu", available=False, reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")
    registry = _real_registry({"local_cpu": provider}, [_cert("local_cpu")])
    candidates = collect_candidates(
        definition=_definition(("local_cpu",)), model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    local_cpu = _by_executor(candidates)["local_cpu"]
    assert local_cpu.certified is True
    assert local_cpu.available is False
    assert local_cpu.reason_code == "EXECUTION_CAPABILITY_UNAVAILABLE"
    assert local_cpu.safe_reason_message == "Executor is currently unavailable."


# Case 4 — input incompatible
def test_candidate_input_incompatible():
    registry = _real_registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    candidates = collect_candidates(
        definition=_definition(("local_cpu",), label_space="spacenet_14"),
        model_release=None,
        probe_recording=_recording(label_space="signal_presence_v1"),
        executor_registry=registry,
    )
    local_cpu = _by_executor(candidates)["local_cpu"]
    assert local_cpu.available is False
    assert local_cpu.reason_code == "INPUT_INCOMPATIBLE"


# Case 5 — remote provider unavailable (no real SSH/network)
def test_candidate_remote_provider_unavailable():
    registry = _real_registry({"remote_gpu": _provider("remote_gpu")}, [_cert("remote_gpu")])
    candidates = collect_candidates(
        definition=_definition(("remote_gpu",)), model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    remote = _by_executor(candidates)["remote_gpu"]
    assert remote.available is False
    assert remote.reason_code == "REMOTE_EXECUTOR_UNAVAILABLE"


# Case 6 — 2 / 3 runnable candidates consume the Task-1 policy ranking
def test_two_runnable_uses_small_ranking():
    providers = {"local_cpu": _provider("local_cpu"), "local_gpu": _provider("local_gpu")}
    certs = [_cert("local_cpu"), _cert("local_gpu")]
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu", "local_gpu")), model_release=None,
        probe_recording=_recording(num_samples=1000, duration_s=0.01),
        executor_registry=_real_registry(providers, certs),
    )
    assert selection.resolved_executor == "local_cpu"
    assert selection.reason_code == "AUTO_LOCAL_CPU_PREFERRED"


def test_three_runnable_uses_gpu_beneficial_ranking():
    providers = {
        "local_cpu": _provider("local_cpu"),
        "local_gpu": _provider("local_gpu"),
        "remote_gpu": _provider("remote_gpu", launcher=_StubLauncher()),
    }
    certs = [_cert("local_cpu"), _cert("local_gpu"), _cert("remote_gpu")]
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu", "local_gpu", "remote_gpu")), model_release=None,
        probe_recording=_recording(), executor_registry=_real_registry(providers, certs),
        dataset_item_count=8,
    )
    assert selection.workload_class == "GPU_BENEFICIAL"
    assert selection.resolved_executor == "local_gpu"
    assert selection.reason_code == "AUTO_LOCAL_GPU_PREFERRED"


# Case 7 — zero runnable
def test_zero_runnable_fails_closed():
    provider = _provider("local_cpu", available=False, reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu",)), model_release=None,
        probe_recording=_recording(),
        executor_registry=_real_registry({"local_cpu": provider}, [_cert("local_cpu")]),
    )
    assert selection.resolved_executor is None
    assert selection.reason_code == "AUTO_NO_RUNNABLE_EXECUTOR"


# Case 8 — dataset live-availability exclusion (GPU_BENEFICIAL, local_gpu unavailable)
def test_dataset_scope_excludes_live_unavailable_local_gpu():
    providers = {
        "local_cpu": _provider("local_cpu"),
        "local_gpu": _provider("local_gpu", available=False, reason_code="EXECUTION_CAPABILITY_UNAVAILABLE"),
    }
    certs = [_cert("local_cpu"), _cert("local_gpu")]
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu", "local_gpu")), model_release=None,
        probe_recording=_recording(), executor_registry=_real_registry(providers, certs),
        dataset_item_count=8,
    )
    assert selection.workload_class == "GPU_BENEFICIAL"
    local_gpu = _by_executor(selection.candidates)["local_gpu"]
    assert local_gpu.available is False
    assert "local_gpu" not in [
        c.executor for c in selection.candidates
        if c.technical and c.configured and c.certified and c.available
    ]
    assert selection.resolved_executor == "local_cpu"


# Case 9 — malicious raw provider detail is never exposed
def test_malicious_raw_reason_detail_is_discarded():
    provider = _provider("local_cpu", probe=_MaliciousProbe())
    registry = _real_registry({"local_cpu": provider}, [_cert("local_cpu")])
    candidates = collect_candidates(
        definition=_definition(("local_cpu",)), model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    local_cpu = _by_executor(candidates)["local_cpu"]
    assert local_cpu.available is False
    assert local_cpu.reason_code == "EXECUTION_CAPABILITY_UNAVAILABLE"
    assert local_cpu.safe_reason_message == "Executor is currently unavailable."
    assert r"C:\secret\runtime\python.exe" not in (local_cpu.safe_reason_message or "")
    assert "/root/private/model.pt" not in (local_cpu.safe_reason_message or "")


# Case 10 — available candidate message is None
def test_available_candidate_has_no_reason_message():
    registry = _real_registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    candidates = collect_candidates(
        definition=_definition(("local_cpu",)), model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    local_cpu = _by_executor(candidates)["local_cpu"]
    assert local_cpu.available is True
    assert local_cpu.reason_code is None
    assert local_cpu.safe_reason_message is None


# Selection shape
def test_resolve_auto_execution_returns_full_selection_shape():
    registry = _real_registry({"local_cpu": _provider("local_cpu")}, [_cert("local_cpu")])
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu",)), model_release=None,
        probe_recording=_recording(), executor_registry=registry,
    )
    assert isinstance(selection, ExecutionSelection)
    assert selection.requested_mode == "auto"
    assert selection.resolved_executor == "local_cpu"
    assert selection.reason_code == "AUTO_ONLY_RUNNABLE_EXECUTOR"
    assert selection.reason == "The only runnable executor was selected."
    assert selection.workload_class == "SMALL"
    assert all(isinstance(c, ExecutionCandidate) for c in selection.candidates)


def test_dataset_scope_ignores_representative_recording_size():
    # An 8-item dataset whose representative recording is tiny must still classify
    # GPU_BENEFICIAL from the frozen item count only.
    providers = {"local_cpu": _provider("local_cpu"), "local_gpu": _provider("local_gpu")}
    certs = [_cert("local_cpu"), _cert("local_gpu")]
    selection = resolve_auto_execution(
        definition=_definition(("local_cpu", "local_gpu")), model_release=None,
        probe_recording=_recording(num_samples=10, duration_s=0.000001),
        executor_registry=_real_registry(providers, certs),
        dataset_item_count=8,
    )
    assert selection.workload_class == "GPU_BENEFICIAL"
    assert selection.resolved_executor == "local_gpu"


def test_resolver_module_is_portable_and_ml_free():
    source = pathlib.Path(resolver.__file__).read_text(encoding="utf-8")
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
                      "subprocess", "ultralytics", "torch", "cuda", "ssh"):
        assert forbidden not in lowered
