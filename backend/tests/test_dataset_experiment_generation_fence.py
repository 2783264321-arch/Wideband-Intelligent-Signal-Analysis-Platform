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


def _running_experiment_with_token(client, token):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    experiment.coordinator_token = token
    session.commit()
    return session, ds, analysis, provider, experiment


def _rotate_token(client, experiment_id, new_token):
    with client.app.state.database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        experiment.coordinator_token = new_token
        session.commit()


def _owned_attempt_with_token(client, token):
    session, ds, analysis, provider, experiment = _running_experiment_with_token(client, token)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id,
        analysis_service=analysis, coordinator_token=token,
    )
    return session, ds, analysis, provider, experiment, item, attempt


def test_matching_token_persisted_and_claim_succeeds(client):
    token = "coord_match"
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt_with_token(client, token)
    with client.app.state.database.session_factory() as fresh:
        stored_exp = fresh.get(DatasetExperimentModel, experiment.id)
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        assert stored_exp.coordinator_token == token
        assert stored_attempt is not None
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"


def test_start_item_attempt_none_token_matches_sealed_behavior(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)  # running, no token (sealed G3-A path)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )
    assert attempt.attempt_number == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"


def test_start_item_attempt_fence_lost_before_transaction_a(client):
    token = "coord_T"
    session, ds, analysis, provider, experiment = _running_experiment_with_token(client, token)
    item = _item(session, experiment.id)
    _rotate_token(client, experiment.id, "coord_T2")

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(
            experiment_id=experiment.id, item_id=item.id,
            analysis_service=analysis, coordinator_token=token,
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
    assert provider.launches == []


def test_launch_item_attempt_fence_lost_before_transaction_b(client):
    token = "coord_T"
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt_with_token(client, token)
    _rotate_token(client, experiment.id, "coord_T2")

    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id=item.id,
            attempt_id=attempt.id, analysis_service=analysis, coordinator_token=token,
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        assert stored_run.status == "pending"
        assert stored_run.worker_pid is None
        assert stored_attempt.launch_requested_at is None
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"


def test_reconcile_fence_lost_no_projection(client):
    token = "coord_T"
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt_with_token(client, token)
    run = session.get(AnalysisRunModel, attempt.analysis_run_id)
    run.status = "completed"
    session.commit()
    _rotate_token(client, experiment.id, "coord_T2")

    with pytest.raises(PlatformError) as exc:
        ds.reconcile_items(experiment.id, coordinator_token=token)
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"
