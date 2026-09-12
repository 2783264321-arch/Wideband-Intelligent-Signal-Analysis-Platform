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


def test_next_attempt_number_is_max_plus_one(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    session.add(AnalysisRunModel(
        id="run_history_1", recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="failed", parameters_json={},
    ))
    session.add(AnalysisRunModel(
        id="run_history_2", recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="failed", parameters_json={},
    ))
    session.add_all([
        DatasetExperimentAttemptModel(id="a1", experiment_item_id=item.id,
                                      attempt_number=1, analysis_run_id="run_history_1"),
        DatasetExperimentAttemptModel(id="a2", experiment_item_id=item.id,
                                      attempt_number=2, analysis_run_id="run_history_2"),
    ])
    session.commit()

    assert ds._next_attempt_number(item.id) == 3


def test_claim_queued_item_is_single_use(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    assert ds._claim_queued_item(item_id=item.id, experiment_id=experiment.id) == 1
    assert ds._claim_queued_item(item_id=item.id, experiment_id=experiment.id) == 0


def test_claim_rejects_externally_started_item(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    with client.app.state.database.session_factory() as other:
        other.get(DatasetExperimentItemModel, item.id).status = "running"
        other.commit()
    assert ds._claim_queued_item(item_id=item.id, experiment_id=experiment.id) == 0


def test_start_item_attempt_real_stale_session_cas(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)  # Session A: loaded queued

    # Session B claims the same Item first and commits.
    with client.app.state.database.session_factory() as winner:
        winner.get(DatasetExperimentItemModel, item.id).status = "running"
        winner.commit()

    # Session A still has stale ORM state showing queued.
    assert item.status == "queued"

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"

    # Winner's committed running state is intact; the loser leaves no
    # Run/Attempt/source-hash and never launches.
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
    assert provider.launches == []


def test_start_item_attempt_synthetic_zero_rowcount_rolls_back(client, monkeypatch):
    # Additional coverage: if the CAS reports zero rows, Transaction A rolls back
    # its own staged work. (The real stale-session race is covered above.)
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    monkeypatch.setattr(ds, "_claim_queued_item", lambda **kwargs: 0)
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
    assert provider.launches == []


def test_attempt_numbers_are_independent_per_item(client):
    _seed_dataset(client, count=2)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    first = _item(session, experiment.id, order=0)
    second = _item(session, experiment.id, order=1)

    a1 = ds.start_item_attempt(experiment_id=experiment.id, item_id=first.id, analysis_service=analysis)
    a2 = ds.start_item_attempt(experiment_id=experiment.id, item_id=second.id, analysis_service=analysis)
    assert a1.attempt_number == 1
    assert a2.attempt_number == 1
