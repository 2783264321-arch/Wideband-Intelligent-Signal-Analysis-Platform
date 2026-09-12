from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.plugin import PluginDeclaration
from app.pipelines.registry import PipelineRegistry
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import ExecutionCertificate, ExecutionCertificateStore, ExecutorRegistry
from app.remote_execution.runtime import RuntimeDescriptor

from executor_fixtures import FakeProvider

PLUGIN_ID = "exp_test_plugin"
PLUGIN_VERSION = "1.0"
LOCAL_REF = "local:test:cpu:1"
REMOTE_REF = "remote:test:cuda:1"
MANIFEST_SHA = "b" * 64

_PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"threshold": {"type": "number"}},
}


class ExpTestPipeline(Pipeline):
    def __init__(self, *, release_required=False, version=PLUGIN_VERSION):
        self._release_required = release_required
        self._version = version

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=PLUGIN_ID,
            name="Experiment Test",
            version=self._version,
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="classification",
            executors_supported=("local_cpu",),
            recommended_executor="local_cpu",
            model_release_required=self._release_required,
            parameter_schema=_PARAM_SCHEMA,
            technical_execution_capabilities=(
                ExecutionCapability("local_cpu", "cpu", "float32"),
            ),
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeReleaseStore:
    def __init__(self, *, release_id="golden", manifest_sha=MANIFEST_SHA):
        self.release_id = release_id
        self.manifest_sha = manifest_sha
        self.resolve_calls = []
        self.reverse_calls = 0

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        rid = requested or self.release_id
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version, model_release_id=rid,
            asset_manifest_sha256=self.manifest_sha,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=self.manifest_sha)
        return ResolvedModelRelease(release=release, manifest=manifest)

    def resolve_by_manifest_sha(self, *args, **kwargs):
        self.reverse_calls += 1
        raise AssertionError("G1 must use resolve() only")


class ExplodingProbe:
    def __init__(self):
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        self.calls.append(recording.id)
        raise AssertionError("G1 must not probe a Recording")


def _certificate(*, release_required, model_release_id, executor, runtime_ref):
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version=PLUGIN_VERSION,
        model_release_id=model_release_id if release_required else None,
        executor=executor, device_type="cpu", precision="float32",
        runtime_ref=runtime_ref, evidence_ref="g1-test",
    )


def _registry(*, release_required=False, certs=None, with_probe=False):
    probe = ExplodingProbe() if with_probe else None
    provider = FakeProvider("local_cpu", runtime_ref=LOCAL_REF, probe=probe)
    if certs is None:
        certs = [_certificate(
            release_required=release_required,
            model_release_id="golden",
            executor="local_cpu",
            runtime_ref=LOCAL_REF,
        )]
    return ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore(list(certs)))


def _service(client, *, pipeline=None, store=None, registry=None):
    session = client.app.state.database.session_factory()
    return DatasetExperimentService(
        session,
        PipelineRegistry([(pipeline or ExpTestPipeline())]),
        store or FakeReleaseStore(),
        registry or _registry(),
    )


def _seed_dataset(client, *, count=3):
    database = client.app.state.database
    with database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()
    return database


def _create(service, **overrides):
    kwargs = dict(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION, executor="local_cpu",
        parameters={}, evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1,
    )
    kwargs.update(overrides)
    return service.create_experiment(**kwargs)


def test_creation_freezes_exact_items_in_manifest_order(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with client.app.state.database.session_factory() as session:
        items = session.query(DatasetExperimentItemModel).order_by(
            DatasetExperimentItemModel.manifest_order
        ).all()
        expected = DatasetBenchmarkService(session).prepare_manifest(
            "SpaceNet", "test", "spacenet_14"
        )
    assert experiment.status == "pending"
    assert len(items) == expected.expected_recordings == 3
    assert [i.manifest_order for i in items] == [e.manifest_order for e in expected.entries]
    assert [i.recording_id for i in items] == [e.recording_id for e in expected.entries]
    assert all(i.status == "queued" for i in items)
    assert experiment.recording_manifest_hash == expected.recording_manifest_hash


def test_creation_creates_zero_attempts_and_zero_analysis_runs_and_no_evaluation(client):
    _seed_dataset(client)
    service = _service(client)
    _create(service)
    with client.app.state.database.session_factory() as session:
        assert session.query(DatasetExperimentAttemptModel).count() == 0
        assert session.query(AnalysisRunModel).count() == 0
        assert session.query(DatasetEvaluationModel).count() == 0


def test_creation_ignores_historical_completed_runs(client):
    _seed_dataset(client)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_old", recording_id="rec_0", pipeline_id=PLUGIN_ID,
            pipeline_version=PLUGIN_VERSION, executor="local_cpu", status="completed",
            parameters_json={},
        ))
        session.commit()
    service = _service(client)
    experiment = _create(service)
    with client.app.state.database.session_factory() as session:
        assert session.query(AnalysisRunModel).count() == 1
        assert session.query(DatasetExperimentAttemptModel).count() == 0
    assert experiment.dataset_evaluation_id is None


def test_creation_freezes_parameters_and_runtime_descriptor(client):
    _seed_dataset(client)
    service = _service(client, registry=_registry(with_probe=True))
    experiment = _create(service, parameters={"threshold": 0.5})
    assert experiment.parameters_json == {"threshold": 0.5}
    assert experiment.executor == "local_cpu"
    assert experiment.runtime_descriptor_json == FakeProvider("local_cpu", runtime_ref=LOCAL_REF).runtime_descriptor().to_metadata()


def test_creation_does_not_probe_any_recording(client):
    _seed_dataset(client)
    registry = _registry(with_probe=True)
    probe = registry.provider("local_cpu")._probe
    _service(client, registry=registry).create_experiment(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION, executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )
    assert probe.calls == []


def test_creation_freezes_release_id_and_manifest_sha(client):
    _seed_dataset(client)
    store = FakeReleaseStore()
    service = _service(client, pipeline=ExpTestPipeline(release_required=True),
                      store=store, registry=_registry(release_required=True))
    experiment = _create(service, model_release_id="golden")
    assert experiment.model_release_id == "golden"
    assert experiment.asset_manifest_sha256 == MANIFEST_SHA
    assert store.resolve_calls == [(PLUGIN_ID, PLUGIN_VERSION, "golden")]
    assert store.reverse_calls == 0


def test_creation_release_less_keeps_release_fields_null(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    assert experiment.model_release_id is None
    assert experiment.asset_manifest_sha256 is None


def test_creation_rejects_release_for_release_less_plugin(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, model_release_id="golden")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"


def test_creation_rejects_max_concurrency_below_one(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, max_concurrency=0)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_creation_rejects_empty_dataset(client):
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service)
    assert exc.value.code == "DATASET_SNAPSHOT_EMPTY"


def test_creation_rejects_wrong_plugin_version(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, plugin_version="9.9")
    assert exc.value.code == "PLUGIN_NOT_FOUND"


def test_creation_rejects_invalid_parameters(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, parameters={"unknown": 1})
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_creation_rejects_unsupported_executor(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, executor="remote_gpu")
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"


def test_creation_rejects_missing_exact_certificate(client):
    _seed_dataset(client)
    service = _service(client, registry=_registry(certs=[]))
    with pytest.raises(PlatformError) as exc:
        _create(service)
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_creation_rejects_unknown_evaluation_protocol(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, evaluation_protocol="not_a_protocol")
    assert exc.value.code == "UNSUPPORTED_EVALUATION_PROTOCOL"


def test_creation_is_atomic_when_item_staging_fails(client, monkeypatch):
    _seed_dataset(client)
    service = _service(client)

    def explode(*args, **kwargs):
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(DatasetExperimentService, "_new_item_rows", explode)
    with pytest.raises(RuntimeError):
        _create(service)
    with client.app.state.database.session_factory() as session:
        assert session.query(DatasetExperimentModel).count() == 0
        assert session.query(DatasetExperimentItemModel).count() == 0
