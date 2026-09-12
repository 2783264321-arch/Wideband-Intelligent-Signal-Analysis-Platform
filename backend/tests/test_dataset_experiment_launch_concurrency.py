from datetime import datetime, timezone

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


class RaisingLocalProvider(FakeProvider):
    def launch(self, run_id, *, coordinator_token):
        raise RuntimeError("boom")


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


def _owned_attempt(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )
    return session, ds, analysis, provider, experiment, item, attempt


def _launch(ds, experiment, item, attempt, analysis):
    return ds.launch_item_attempt(
        experiment_id=experiment.id, item_id=item.id,
        attempt_id=attempt.id, analysis_service=analysis,
    )


def test_claim_launch_intent_real_stale_session(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    assert attempt.launch_requested_at is None

    now = datetime.now(timezone.utc)
    with client.app.state.database.session_factory() as winner:
        winner.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at = now
        winner.commit()

    # Session A still has stale NULL ORM state: the real CAS must lose.
    assert attempt.launch_requested_at is None
    assert ds._claim_launch_intent(attempt_id=attempt.id, item_id=item.id, requested_at=now) == 0


def test_launch_item_attempt_real_stale_session_does_not_launch(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)

    with client.app.state.database.session_factory() as winner:
        winner.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at = datetime.now(timezone.utc)
        winner.commit()

    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []  # loser never reaches physical launch

    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is not None


