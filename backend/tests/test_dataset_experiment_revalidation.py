from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
    RuntimeDescriptor,
)

from executor_fixtures import FakeProvider

PLUGIN_ID = "exp_test_plugin"
PLUGIN_VERSION = "1.0"
LOCAL_REF = "local:test:cpu:1"
MANIFEST_SHA = "b" * 64

_PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"threshold": {"type": "number"}},
}


class ExpTestPipeline(Pipeline):
    def __init__(self, *, release_required=False, version=PLUGIN_VERSION, plugin_id=PLUGIN_ID):
        self._release_required = release_required
        self._version = version
        self._plugin_id = plugin_id

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=self._plugin_id, name="Experiment Test", version=self._version,
            label_space="spacenet_14", recommended_device="CPU", cpu_supported=True,
            stages=(), inspectable_stages=(), task_capability="classification",
            executors_supported=("local_cpu",), recommended_executor="local_cpu",
            model_release_required=self._release_required, parameter_schema=_PARAM_SCHEMA,
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeReleaseStore:
    def __init__(self, *, release_id="golden", manifest_sha=MANIFEST_SHA):
        self.release_id = release_id
        self.manifest_sha = manifest_sha
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        rid = requested or self.release_id
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version, model_release_id=rid,
            asset_manifest_sha256=self.manifest_sha,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=self.manifest_sha)
        return ResolvedModelRelease(release=release, manifest=manifest)


class MissingReleaseStore:
    def resolve(self, plugin_id, plugin_version, requested):
        raise PlatformError("MODEL_RELEASE_NOT_FOUND", "gone")


class DriftingProvider(FakeProvider):
    """Same runtime_ref/cert tuple, different frozen descriptor metadata."""

    def runtime_descriptor(self) -> RuntimeDescriptor:
        base = super().runtime_descriptor()
        return RuntimeDescriptor(
            executor=base.executor, device_type=base.device_type,
            device_index=base.device_index, precision=base.precision,
            environment_ref=base.environment_ref, environment_label="drifted",
        )


def _certificate(*, release_required, model_release_id, executor, runtime_ref):
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version=PLUGIN_VERSION,
        model_release_id=model_release_id if release_required else None,
        executor=executor, device_type="cpu", precision="float32",
        runtime_ref=runtime_ref, evidence_ref="g1-test",
    )


def _registry(*, release_required=False, certs=None, provider=None):
    provider = provider or FakeProvider("local_cpu", runtime_ref=LOCAL_REF)
    if certs is None:
        certs = [_certificate(
            release_required=release_required, model_release_id="golden",
            executor="local_cpu", runtime_ref=LOCAL_REF,
        )]
    return ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore(list(certs)))


_UNSET = object()


def _service(client, *, pipeline=None, store=_UNSET, registry=None):
    session = client.app.state.database.session_factory()
    return DatasetExperimentService(
        session,
        PipelineRegistry([(pipeline or ExpTestPipeline())]),
        FakeReleaseStore() if store is _UNSET else store,
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


def _default_kwargs():
    return dict(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION, executor="local_cpu",
        parameters={}, evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1,
    )


def _create(service, **overrides):
    return service.create_experiment(**{**_default_kwargs(), **overrides})


def _update_experiment(client, experiment_id, **values):
    with client.app.state.database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        for key, value in values.items():
            setattr(experiment, key, value)
        session.commit()


def test_unchanged_identity_passes(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    revalidated = service.revalidate_frozen_identity(experiment.id)
    assert revalidated.id == experiment.id
    assert revalidated.status == "pending"


def test_manifest_hash_drift_fails(client):
    database = _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with database.session_factory() as session:
        add_ground_truth(session, gt_id="gt_extra", recording_id="rec_0", class_id=6,
                         class_name="BLE LE1M", t0=0.07, t1=0.08,
                         f0=2_441_200_000.0, f1=2_441_300_000.0)
        session.commit()
    with pytest.raises(PlatformError) as exc:
        service.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_dataset_snapshot_no_longer_resolves_as_identity_changed(client):
    database = _seed_dataset(client)
    experiment = _create(_service(client))
    with database.session_factory() as session:
        for recording in session.query(RecordingModel).all():
            recording.has_ground_truth = False
        session.commit()
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"
    assert exc.value.status_code == 409


def test_item_membership_corruption_fails(client):
    database = _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with database.session_factory() as session:
        victim = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=2
        ).one()
        session.delete(victim)
        session.commit()
    with pytest.raises(PlatformError) as exc:
        service.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_item_order_corruption_fails(client):
    database = _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with database.session_factory() as session:
        first = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=0
        ).one()
        first.manifest_order = 5  # unique constraint stays satisfied
        session.commit()
    with pytest.raises(PlatformError) as exc:
        service.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_plugin_version_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    drifting = _service(client, pipeline=ExpTestPipeline(version="2.0"))
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_plugin_removed_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    missing = _service(client, pipeline=ExpTestPipeline(plugin_id="other_plugin"))
    with pytest.raises(PlatformError) as exc:
        missing.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_parameters_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    _update_experiment(client, experiment.id, parameters_json={"unknown": 1})
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_release_drift_fails(client):
    _seed_dataset(client)
    create_service = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(), registry=_registry(release_required=True),
    )
    experiment = _create(create_service, model_release_id="golden")
    drifting = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(manifest_sha="c" * 64), registry=_registry(release_required=True),
    )
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_release_no_longer_resolves_fails(client):
    _seed_dataset(client)
    create_service = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(), registry=_registry(release_required=True),
    )
    experiment = _create(create_service, model_release_id="golden")
    drifting = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=MissingReleaseStore(), registry=_registry(release_required=True),
    )
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_certificate_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    unmatched = _service(client, registry=_registry(certs=[]))
    with pytest.raises(PlatformError) as exc:
        unmatched.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_runtime_descriptor_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    drifting = _service(client, registry=_registry(provider=DriftingProvider("local_cpu", runtime_ref=LOCAL_REF)))
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_missing_experiment_fails_closed(client):
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity("does-not-exist")
    assert exc.value.code == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"


def test_evaluation_protocol_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    _update_experiment(client, experiment.id, evaluation_protocol="retired_protocol_v9")
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_invalid_frozen_max_concurrency_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    _update_experiment(client, experiment.id, max_concurrency=0)
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_missing_release_store_fails_closed(client):
    _seed_dataset(client)
    create_service = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(), registry=_registry(release_required=True),
    )
    experiment = _create(create_service, model_release_id="golden")
    drifting = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=None, registry=_registry(release_required=True),
    )
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_validation_never_mutates_frozen_identity(client):
    _seed_dataset(client)
    experiment = _create(_service(client))

    # Deliberately corrupt one frozen field and durably commit the corruption.
    _update_experiment(client, experiment.id, parameters_json={"unknown": 1})

    # Snapshot the CORRUPTED persisted state, not the original valid state.
    with client.app.state.database.session_factory() as session:
        stored = session.get(DatasetExperimentModel, experiment.id)
        corrupted_snapshot = {
            "dataset_name": stored.dataset_name,
            "dataset_split": stored.dataset_split,
            "dataset_label_space": stored.dataset_label_space,
            "recording_manifest_hash": stored.recording_manifest_hash,
            "plugin_id": stored.plugin_id,
            "plugin_version": stored.plugin_version,
            "model_release_id": stored.model_release_id,
            "asset_manifest_sha256": stored.asset_manifest_sha256,
            "parameters_json": dict(stored.parameters_json or {}),
            "executor": stored.executor,
            "runtime_descriptor_json": dict(stored.runtime_descriptor_json or {}),
            "evaluation_protocol": stored.evaluation_protocol,
            "max_concurrency": stored.max_concurrency,
            "status": stored.status,
        }
    assert corrupted_snapshot["parameters_json"] == {"unknown": 1}

    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"

    # Revalidation detected the drift but MUST NOT repair/mutate any frozen field.
    with client.app.state.database.session_factory() as session:
        stored = session.get(DatasetExperimentModel, experiment.id)
        for key, value in corrupted_snapshot.items():
            assert getattr(stored, key) == value, key
