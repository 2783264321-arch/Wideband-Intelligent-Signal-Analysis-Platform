from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
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

from executor_fixtures import FakeProvider, FakeRegistry


class G3LocalPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="g3_local", name="G3 Local", version="1.0", label_space="spacenet_14",
            recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
            task_capability="classification", executors_supported=("local_cpu",),
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
            parameter_schema={},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


def _seed_dataset(client, count=2):
    with client.app.state.database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _services(client, *, provider=None):
    session = client.app.state.database.session_factory()
    provider = provider or FakeProvider("local_cpu")
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(
        session, registry, client.app.state.job_manager, executor_registry=executor_registry,
    )
    return session, ds, analysis, provider


def _experiment(ds, *, status="running"):
    """Create an experiment and explicitly transition it to the requested state.

    G3-A only schedules a ``running`` experiment; G3-C owns ``pending -> running``.
    """
    experiment = ds.create_experiment(
        name="g3", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_local", plugin_version="1.0",
        executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )
    if status != "pending":
        experiment.status = status
        ds.session.commit()
    return experiment


def _item(session, experiment_id, order=0):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=order
    ).one()


def test_start_item_attempt_binds_run_attempt_and_running_item(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )

    assert attempt.attempt_number == 1
    assert attempt.launch_requested_at is None
    assert provider.launches == []  # no physical launch
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        stored_item = fresh.get(DatasetExperimentItemModel, item.id)
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        assert run.status == "pending"
        assert run.executor == "local_cpu"
        assert stored_item.status == "running"
        assert stored_attempt is not None


def test_start_item_attempt_uses_frozen_parameters_and_identity(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    captured = {}
    real_prepare = analysis.prepare_run

    def spy_prepare(**kwargs):
        captured.update(kwargs)
        return real_prepare(**kwargs)

    monkeypatch.setattr(analysis, "prepare_run", spy_prepare)
    ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)

    assert captured["recording_id"] == item.recording_id
    assert captured["pipeline_id"] == "g3_local"
    assert captured["executor"] == "local_cpu"
    assert captured["parameters"] == {}
    assert captured["model_release_id"] is None


def test_start_item_attempt_requires_same_session(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    other_session = client.app.state.database.session_factory()
    other_analysis = AnalysisService(
        other_session, PipelineRegistry([G3LocalPipeline()]), client.app.state.job_manager,
        executor_registry=FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
    )
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=other_analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_rejects_non_queued_item(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    item.status = "completed"
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_rejects_terminal_experiment(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds, status="completed")
    item = _item(session, experiment.id)

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_start_item_attempt_rejects_pending_experiment(client):
    # G3-C owns Experiment.pending -> running; G3-A must not create a running
    # Item under a pending Experiment.
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds, status="pending")
    item = _item(session, experiment.id)

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_missing_item_fails_closed(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id="missing", analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_ITEM_NOT_FOUND"


def test_start_item_attempt_prepare_failure_leaves_item_queued(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    def boom(**kwargs):
        raise PlatformError("EXECUTION_CAPABILITY_UNAVAILABLE", "injected")

    monkeypatch.setattr(analysis, "prepare_run", boom)
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_commit_failure_rolls_back(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_revalidates_frozen_identity(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    experiment.parameters_json = {"unexpected": 1}  # drift vs empty schema
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
