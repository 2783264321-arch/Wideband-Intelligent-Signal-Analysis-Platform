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


def _seed_dataset(client, count=3):
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


def _experiment(ds):
    experiment = ds.create_experiment(
        name="g3", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_local", plugin_version="1.0",
        executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )
    experiment.status = "running"
    ds.session.commit()
    return experiment


def _item(session, experiment_id, order=0):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=order
    ).one()


def _add_run(session, *, item, run_id, status, pipeline_id="g3_local",
             pipeline_version="1.0", executor="local_cpu", recording_id=None):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=recording_id or item.recording_id,
        pipeline_id=pipeline_id, pipeline_version=pipeline_version, executor=executor,
        status=status, parameters_json={},
        error_type=("ANALYSIS_FAILED" if status == "failed" else None),
        error_message=("boom" if status == "failed" else None),
    ))
    session.commit()


def _add_attempt(session, *, item, attempt_id, attempt_number, run_id):
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=item.id,
        attempt_number=attempt_number, analysis_run_id=run_id,
    ))
    session.commit()


def _setup(client, *, count=3):
    _seed_dataset(client, count=count)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    return session, ds, analysis, provider, experiment


def test_queued_no_attempts_valid(client):
    session, ds, analysis, provider, experiment = _setup(client)
    summary = ds.reconcile_items(experiment.id)
    assert summary.queued == 3


def test_queued_latest_failed_valid(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    _add_run(session, item=item, run_id="run_1", status="failed")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    summary = ds.reconcile_items(experiment.id)
    assert summary.queued == 3
    assert summary.failed == 0


def test_queued_latest_interrupted_valid(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    _add_run(session, item=item, run_id="run_1", status="interrupted")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    assert ds.reconcile_items(experiment.id).queued == 3


def test_queued_latest_completed_invariant(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    _add_run(session, item=item, run_id="run_1", status="completed")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_queued_active_pending_or_running_invariant(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    _add_run(session, item=item, run_id="run_1", status="running")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_active_attempt_must_be_latest(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "running"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="running")
    _add_run(session, item=item, run_id="run_2", status="completed")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    _add_attempt(session, item=item, attempt_id="a2", attempt_number=2, run_id="run_2")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_terminal_item_with_hidden_older_active_attempt_invariant(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "completed"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="running")
    _add_run(session, item=item, run_id="run_2", status="completed")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    _add_attempt(session, item=item, attempt_id="a2", attempt_number=2, run_id="run_2")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_historical_attempt_missing_run_invariant(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="missing_run")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_historical_attempt_identity_mismatch_invariant(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    _add_run(session, item=item, run_id="run_1", status="completed", pipeline_id="other_plugin")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_running_pending_run_stays_running(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "running"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="pending")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    summary = ds.reconcile_items(experiment.id)
    assert summary.running == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"


def test_running_completed_run_becomes_completed(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "running"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="completed")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    summary = ds.reconcile_items(experiment.id)
    assert summary.completed == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "completed"


def test_running_failed_run_becomes_failed_and_projects_error(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "running"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="failed")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    summary = ds.reconcile_items(experiment.id)
    assert summary.failed == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentItemModel, item.id)
        assert stored.status == "failed"
        assert stored.last_error_type == "ANALYSIS_FAILED"
        assert stored.last_error_message == "boom"


def test_running_interrupted_run_becomes_failed(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "running"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="interrupted")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    assert ds.reconcile_items(experiment.id).failed == 1


def test_completed_requires_completed_latest_run(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "completed"
    session.commit()
    _add_run(session, item=item, run_id="run_1", status="pending")
    _add_attempt(session, item=item, attempt_id="a1", attempt_number=1, run_id="run_1")
    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_failed_without_attempt_allowed(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item = _item(session, experiment.id)
    item.status = "failed"
    session.commit()
    summary = ds.reconcile_items(experiment.id)
    assert summary.failed == 1


def test_reconciliation_all_or_nothing(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item1 = _item(session, experiment.id, order=0)
    item2 = _item(session, experiment.id, order=1)
    item1.status = "running"
    item2.status = "running"
    session.commit()
    _add_run(session, item=item1, run_id="run_ok", status="completed")
    _add_attempt(session, item=item1, attempt_id="a1", attempt_number=1, run_id="run_ok")
    _add_attempt(session, item=item2, attempt_id="a2", attempt_number=1, run_id="missing_run")

    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item1.id).status == "running"
        assert fresh.get(DatasetExperimentItemModel, item2.id).status == "running"


def test_select_queued_items_manifest_order_and_limit(client):
    session, ds, analysis, provider, experiment = _setup(client)
    item0 = _item(session, experiment.id, order=0)
    item1 = _item(session, experiment.id, order=1)
    item2 = _item(session, experiment.id, order=2)
    item1.status = "running"
    session.commit()
    assert ds.select_queued_items(experiment.id, limit=5) == [item0.id, item2.id]
    assert ds.select_queued_items(experiment.id, limit=1) == [item0.id]
    assert ds.select_queued_items(experiment.id, limit=0) == []
