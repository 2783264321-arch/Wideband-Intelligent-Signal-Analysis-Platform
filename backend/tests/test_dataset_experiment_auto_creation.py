from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.core.errors import PlatformError
from app.dataset_experiments.model import DatasetExperimentItemModel, DatasetExperimentModel
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
    RuntimeDescriptor,
)

from executor_fixtures import FakeProvider

_DEVICE = {
    "local_cpu": ("cpu", "float32"),
    "local_gpu": ("cuda", "float16"),
    "remote_gpu": ("cuda", "float16"),
}
PLUGIN_ID = "auto_exp"
MANIFEST_SHA = "b" * 64


class AutoExpPipeline(Pipeline):
    def __init__(self, *, release_required=False):
        self._release_required = release_required

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=PLUGIN_ID,
            name="Auto Experiment",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="classification",
            executors_supported=("local_cpu", "local_gpu"),
            recommended_executor="local_cpu",
            technical_execution_capabilities=(
                ExecutionCapability("local_cpu", "cpu", "float32"),
                ExecutionCapability("local_gpu", "cuda", "float16"),
            ),
            parameter_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"threshold": {"type": "number"}},
            },
            model_release_required=self._release_required,
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeReleaseStore:
    def __init__(self, *, release_id="golden"):
        self.release_id = release_id
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version,
            model_release_id=requested or self.release_id,
            asset_manifest_sha256=MANIFEST_SHA,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST_SHA)
        return ResolvedModelRelease(release=release, manifest=manifest)


class RecordingJobManagerStub:
    def __init__(self, pid=4242):
        self.calls = []
        self.pid = pid

    def start(self, experiment_id, coordinator_token):
        self.calls.append((experiment_id, coordinator_token))
        return self.pid


def _provider(executor, *, available=True, reason_code=None):
    device_type, precision = _DEVICE[executor]
    return FakeProvider(
        executor, device_type=device_type, precision=precision,
        available=available, reason_code=reason_code,
    )


def _cert(executor, *, model_release_id=None):
    device_type, precision = _DEVICE[executor]
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version="1.0", model_release_id=model_release_id,
        executor=executor, device_type=device_type, precision=precision,
        runtime_ref=f"fake:{executor}", evidence_ref="test",
    )


def _registry(providers, certs):
    return ExecutorRegistry(dict(providers), ExecutionCertificateStore(list(certs)))


def _seed_dataset(client, *, count=3):
    with client.app.state.database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _service(client, *, pipeline=None, store=None, registry=None):
    session = client.app.state.database.session_factory()
    return DatasetExperimentService(
        session,
        PipelineRegistry([pipeline or AutoExpPipeline()]),
        store or FakeReleaseStore(),
        registry or _registry(
            {"local_cpu": _provider("local_cpu"), "local_gpu": _provider("local_gpu")},
            [_cert("local_cpu"), _cert("local_gpu")],
        ),
    )


def _create(service, **overrides):
    kwargs = dict(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID, plugin_version="1.0",
        parameters={}, evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )
    kwargs.update(overrides)
    return service.create_experiment(**kwargs)


def _experiment_count(client):
    with client.app.state.database.session_factory() as session:
        return session.query(DatasetExperimentModel).count()


# 1. manual + omitted executor -> fail closed
def test_manual_without_executor_is_rejected(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, execution_mode="manual")
    assert exc.value.code == "EXECUTION_REQUEST_INVALID"
    assert _experiment_count(client) == 0


# 2. manual + explicit executor -> unchanged
def test_manual_explicit_executor_is_unchanged(client):
    _seed_dataset(client)
    experiment = _create(_service(client), executor="local_cpu")
    assert experiment.executor == "local_cpu"
    assert experiment.status == "pending"


# 3. auto + explicit executor -> conflict, no row
def test_auto_with_explicit_executor_is_rejected(client):
    _seed_dataset(client)
    with pytest.raises(PlatformError) as exc:
        _create(_service(client), execution_mode="auto", executor="local_cpu")
    assert exc.value.code == "EXECUTION_REQUEST_INVALID"
    assert _experiment_count(client) == 0


# 4. auto + omitted -> resolve once, freeze executor + provenance
def test_auto_resolves_and_freezes_executor(client):
    _seed_dataset(client, count=3)
    experiment = _create(_service(client), execution_mode="auto")
    assert experiment.executor == "local_cpu"
    assert experiment.executor != "auto"
    selection = experiment.runtime_descriptor_json["execution_selection"]
    assert selection["requested_execution_mode"] == "auto"
    assert selection["resolved_executor"] == "local_cpu"
    assert selection["auto_reason_code"] == "AUTO_LOCAL_CPU_PREFERRED"
    assert selection["workload_class"] == "SMALL"


# 5. auto + item count >= 8 -> GPU_BENEFICIAL
def test_auto_large_dataset_is_gpu_beneficial(client):
    _seed_dataset(client, count=8)
    experiment = _create(_service(client), execution_mode="auto")
    selection = experiment.runtime_descriptor_json["execution_selection"]
    assert selection["workload_class"] == "GPU_BENEFICIAL"
    assert experiment.executor == "local_gpu"


# 6. auto + zero runnable -> fail closed, no row
def test_auto_zero_runnable_is_rejected(client):
    _seed_dataset(client)
    registry = _registry(
        {"local_cpu": _provider("local_cpu", available=False,
                                reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
        [_cert("local_cpu")],
    )
    with pytest.raises(PlatformError) as exc:
        _create(_service(client, registry=registry), execution_mode="auto")
    assert exc.value.code == "AUTO_NO_RUNNABLE_EXECUTOR"
    assert _experiment_count(client) == 0


# 7. dataset live-availability exclusion: local_gpu unavailable is not frozen
def test_auto_excludes_live_unavailable_local_gpu(client):
    _seed_dataset(client, count=8)
    registry = _registry(
        {"local_cpu": _provider("local_cpu"),
         "local_gpu": _provider("local_gpu", available=False,
                                reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
        [_cert("local_cpu"), _cert("local_gpu")],
    )
    experiment = _create(_service(client, registry=registry), execution_mode="auto")
    assert experiment.executor == "local_cpu"
    assert experiment.runtime_descriptor_json["execution_selection"]["workload_class"] == "GPU_BENEFICIAL"


# 8. deterministic representative probe recording + resolve once
def test_auto_probes_smallest_manifest_order_recording_once(client, monkeypatch):
    import app.dataset_experiments.service as service_module

    observed = []
    real = service_module.resolve_auto_execution

    def spy(*, definition, model_release, probe_recording, executor_registry, dataset_item_count=None):
        observed.append((probe_recording.id, dataset_item_count))
        return real(
            definition=definition, model_release=model_release,
            probe_recording=probe_recording, executor_registry=executor_registry,
            dataset_item_count=dataset_item_count,
        )

    monkeypatch.setattr(service_module, "resolve_auto_execution", spy)
    _seed_dataset(client, count=3)
    _create(_service(client), execution_mode="auto")
    assert len(observed) == 1  # resolve ONCE
    assert observed[0] == ("rec_0", 3)  # smallest manifest_order entry + frozen item count


# 9. A1 descriptor identity preserved despite the extra namespace key
def test_execution_selection_namespace_is_inert_for_runtime_descriptor(client):
    _seed_dataset(client)
    provider = _provider("local_cpu")
    registry = _registry({"local_cpu": provider}, [_cert("local_cpu")])
    experiment = _create(_service(client, registry=registry), execution_mode="auto")
    parsed = RuntimeDescriptor.from_metadata(experiment.runtime_descriptor_json)
    assert parsed is not None
    assert parsed.to_metadata() == provider.runtime_descriptor().to_metadata()
    assert "execution_selection" in experiment.runtime_descriptor_json


# 10. scientific parameters untouched
def test_auto_does_not_touch_parameters_json(client):
    _seed_dataset(client)
    experiment = _create(_service(client), execution_mode="auto", parameters={"threshold": 0.5})
    assert experiment.parameters_json == {"threshold": 0.5}


# 11. retry_failed keeps the frozen executor and never re-resolves Auto
def test_retry_failed_keeps_frozen_executor(client, monkeypatch):
    import app.dataset_experiments.service as service_module

    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service, execution_mode="auto")

    with client.app.state.database.session_factory() as session:
        stored = session.get(DatasetExperimentModel, experiment.id)
        stored.status = "completed_with_failures"
        stored.completed_at = datetime.now(timezone.utc)
        item = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id
        ).first()
        item.status = "failed"
        item.last_error_type = "ANALYSIS_FAILED"
        session.commit()

    def _boom(*args, **kwargs):
        raise AssertionError("retry must not re-resolve Auto")

    monkeypatch.setattr(service_module, "resolve_auto_execution", _boom)
    service.session.expire_all()  # reload the externally-mutated experiment row
    retried = service.retry_failed(experiment.id, RecordingJobManagerStub())
    assert retried.executor == "local_cpu"


# 12. safe public readback exposes provenance fields
def test_readback_exposes_safe_execution_selection_projection(client):
    _seed_dataset(client)
    experiment = _create(_service(client), execution_mode="auto")
    response = client.get(f"/api/dataset-experiments/{experiment.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["requested_execution_mode"] == "auto"
    assert body["auto_reason_code"] == "AUTO_LOCAL_CPU_PREFERRED"
    assert body["auto_reason"] == "Local CPU was selected."
    assert body["workload_class"] == "SMALL"
    # The projected Auto reason is platform-owned and path-free. (The raw
    # runtime_descriptor_json exposure is pre-existing API debt, out of A2 scope.)
    assert "/fake/" not in body["auto_reason"]
    assert "C:" not in body["auto_reason"]


# 13. historical experiment without the namespace stays readable
def test_historical_experiment_readback_is_none(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service, executor="local_cpu")
    # Simulate a historical experiment whose descriptor has no execution_selection key.
    with client.app.state.database.session_factory() as session:
        stored = session.get(DatasetExperimentModel, experiment.id)
        descriptor = dict(stored.runtime_descriptor_json)
        descriptor.pop("execution_selection", None)
        stored.runtime_descriptor_json = descriptor
        session.commit()
    response = client.get(f"/api/dataset-experiments/{experiment.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["requested_execution_mode"] is None
    assert body["auto_reason_code"] is None
    assert body["workload_class"] is None
