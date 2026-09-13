"""Plan A1 / Task 6 — no-GPU / profile-change recovery boundary matrix.

Historical DB state contains local_gpu rows, but the new deployment has no
local_gpu provider. Control plane must boot; no stale pending/running state; no
executor substitution; Run terminal before Item failed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.benchmarks.service import DatasetBenchmarkService
from app.dataset_experiments.coordinator import DatasetExperimentCoordinator
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.recovery import recover_dataset_experiments
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.recovery import mark_stale_local_runs_interrupted
from app.remote_execution.runtime import RuntimeDescriptor

from executor_fixtures import FakeRegistry


class G3LocalPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="g3_local", name="G3 Local", version="1.0", label_space="spacenet_14",
            recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
            task_capability="classification", executors_supported=("local_gpu",),
            technical_execution_capabilities=(ExecutionCapability("local_gpu", "cuda", "float16"),),
            parameter_schema={},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


FROZEN_DESCRIPTOR = RuntimeDescriptor(
    "local_gpu", "cuda", 0, "float16",
    environment_ref="/gone/python", environment_label="local:autodl_primary:gpu:gone",
)


class _JobManager:
    def __init__(self):
        self.starts = []

    def start(self, experiment_id, coordinator_token):
        self.starts.append((experiment_id, coordinator_token))
        return 4242


def _seed_experiment(session, *, item_status, with_run):
    session.add(RecordingModel(
        id="rec_g", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
        sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
        num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", source_data_sha256="1" * 64,
        has_ground_truth=True,
    ))
    session.flush()
    preview = DatasetBenchmarkService(session).prepare_manifest("SpaceNet", "test", "spacenet_14")
    session.add(DatasetExperimentModel(
        id="exp_g", name="g", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", recording_manifest_hash=preview.recording_manifest_hash,
        plugin_id="g3_local", plugin_version="1.0", model_release_id=None,
        asset_manifest_sha256=None, parameters_json={}, executor="local_gpu",
        runtime_descriptor_json=FROZEN_DESCRIPTOR.to_metadata(),
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
        status="running", coordinator_token="T",
    ))
    session.add(DatasetExperimentItemModel(
        id="item_g", experiment_id="exp_g", manifest_order=0, recording_id="rec_g",
        status=item_status,
    ))
    if with_run:
        session.add(AnalysisRunModel(
            id="run_g", recording_id="rec_g", pipeline_id="g3_local", pipeline_version="1.0",
            executor="local_gpu", status="pending", parameters_json={},
        ))
        session.add(DatasetExperimentAttemptModel(
            id="att_g", experiment_item_id="item_g", attempt_number=1,
            analysis_run_id="run_g", launch_requested_at=None,
        ))
    session.commit()


def _seed(client, *, item_status="running", with_run=True):
    session = client.app.state.database.session_factory()
    _seed_experiment(session, item_status=item_status, with_run=with_run)
    return session


def _services_factory(client):
    def factory(session):
        registry = PipelineRegistry([G3LocalPipeline()])
        executor_registry = FakeRegistry({})  # no local_gpu provider
        ds = DatasetExperimentService(session, registry, None, executor_registry)
        analysis = AnalysisService(
            session, registry, client.app.state.job_manager, executor_registry=executor_registry
        )
        return ds, analysis

    return factory


def test_case_a_local_gpu_running_interrupted_on_startup(client):
    _seed(client, item_status="completed", with_run=False)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_running", recording_id="rec_g", pipeline_id="g3_local",
            pipeline_version="1.0", executor="local_gpu", status="running", parameters_json={},
        ))
        session.commit()
    with client.app.state.database.session_factory() as session:
        n = mark_stale_local_runs_interrupted(session)
    assert n == 1
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, "run_running")
    assert run.status == "interrupted"
    assert run.error_type == "ANALYSIS_INTERRUPTED"


def test_case_b_authority_loss_terminalizes_run_then_item(client):
    session = _seed(client)
    recover_dataset_experiments(
        session, job_manager=_JobManager(), registry=PipelineRegistry([G3LocalPipeline()]),
        model_release_store=None, executor_registry=FakeRegistry({}),
        startup_recovery_cutoff=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, "run_g")
        att = fresh.get(DatasetExperimentAttemptModel, "att_g")
        token = fresh.get(DatasetExperimentModel, "exp_g").coordinator_token
    assert att.launch_requested_at is None
    assert run.status == "interrupted"
    assert run.error_type == "EXECUTION_CAPABILITY_UNAVAILABLE"
    assert run.worker_pid is None

    DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=_services_factory(client),
    ).step("exp_g", token)
    with client.app.state.database.session_factory() as fresh:
        item = fresh.get(DatasetExperimentItemModel, "item_g")
        experiment = fresh.get(DatasetExperimentModel, "exp_g")
    assert item.status == "failed"
    assert experiment.status == "completed_with_failures"


def test_case_c_ambiguous_marker_fails_closed_no_relaunch(client):
    session = _seed(client)
    with client.app.state.database.session_factory() as fresh:
        fresh.get(DatasetExperimentAttemptModel, "att_g").launch_requested_at = datetime.now(timezone.utc)
        fresh.commit()
    recover_dataset_experiments(
        session, job_manager=_JobManager(), registry=PipelineRegistry([G3LocalPipeline()]),
        model_release_store=None, executor_registry=FakeRegistry({}),
        startup_recovery_cutoff=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, "run_g")
        att = fresh.get(DatasetExperimentAttemptModel, "att_g")
    assert run.status == "interrupted"
    assert att.launch_requested_at is not None


def test_case_d_queued_items_terminate_completed_with_failures(client):
    _seed(client, item_status="queued", with_run=False)
    coordinator = DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=_services_factory(client),
    )
    for _ in range(4):
        coordinator.step("exp_g", "T")
    with client.app.state.database.session_factory() as fresh:
        experiment = fresh.get(DatasetExperimentModel, "exp_g")
        item = fresh.get(DatasetExperimentItemModel, "item_g")
    assert item.status == "failed"
    assert experiment.status == "completed_with_failures"
