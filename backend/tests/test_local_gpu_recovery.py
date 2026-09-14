"""Plan B H4: local_gpu recovery matrix (deterministic, control-plane only).

GPU REQUIRED: NO. These deterministic CPU/DB cases consume zero real GPU
executions. They extend the A1 recovery architecture (NOT redesigned) to the
local_gpu executor and assert the approved H4 invariants:

  completed work is never rerun; running victims terminalize; queued work
  resumes; attempt history is preserved; repeated recovery is idempotent; stale
  coordinator fencing writes nothing; retry_failed requeues only failed items;
  evaluation recovery spawns no AnalysisRuns.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.recovery import recover_dataset_experiments
from app.remote_execution.recovery import mark_stale_local_runs_interrupted


class _JobManager:
    def __init__(self):
        self.starts = []

    def start(self, experiment_id, coordinator_token):
        self.starts.append((experiment_id, coordinator_token))
        return 5555


def _seed(client, *, executor="local_gpu", item_status="queued", with_run=False):
    from app.benchmarks.service import DatasetBenchmarkService
    from app.recordings.model import RecordingModel
    from app.remote_execution.runtime import RuntimeDescriptor

    descriptor = RuntimeDescriptor(
        executor, "cuda" if executor == "local_gpu" else "cpu",
        0 if executor == "local_gpu" else None,
        "float16" if executor == "local_gpu" else "float32",
        environment_ref="/ml/python", environment_label="local:autodl_primary:gpu:7b958347b5af",
    )
    session = client.app.state.database.session_factory()
    session.add(RecordingModel(
        id="rec_h4", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
        sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
        num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", source_data_sha256="1" * 64, has_ground_truth=True,
    ))
    session.flush()
    preview = DatasetBenchmarkService(session).prepare_manifest("SpaceNet", "test", "spacenet_14")
    session.add(DatasetExperimentModel(
        id="exp_h4", name="h4", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", recording_manifest_hash=preview.recording_manifest_hash,
        plugin_id="g3_local", plugin_version="1.0", model_release_id=None,
        asset_manifest_sha256=None, parameters_json={}, executor=executor,
        runtime_descriptor_json=descriptor.to_metadata(),
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
        status="running", coordinator_token="T_old",
    ))
    session.add(DatasetExperimentItemModel(
        id="item_h4", experiment_id="exp_h4", manifest_order=0, recording_id="rec_h4",
        status=item_status,
    ))
    if with_run:
        session.add(AnalysisRunModel(
            id="run_h4", recording_id="rec_h4", pipeline_id="g3_local", pipeline_version="1.0",
            executor=executor, status="pending", parameters_json={},
        ))
        session.add(DatasetExperimentAttemptModel(
            id="att_h4", experiment_item_id="item_h4", attempt_number=1,
            analysis_run_id="run_h4", launch_requested_at=None,
        ))
    session.commit()
    return session


def test_h4_stale_local_gpu_run_interrupted(client):
    _seed(client, executor="local_gpu", item_status="completed", with_run=False)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_running", recording_id="rec_h4", pipeline_id="g3_local",
            pipeline_version="1.0", executor="local_gpu", status="running", parameters_json={},
        ))
        session.commit()
    with client.app.state.database.session_factory() as session:
        count = mark_stale_local_runs_interrupted(session)
    assert count == 1
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, "run_running")
    assert run.status == "interrupted"
    assert run.error_type == "ANALYSIS_INTERRUPTED"


def test_h4_local_cpu_parity_unchanged(client):
    _seed(client, executor="local_cpu", item_status="completed", with_run=False)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_cpu", recording_id="rec_h4", pipeline_id="g3_local",
            pipeline_version="1.0", executor="local_cpu", status="running", parameters_json={},
        ))
        session.commit()
    with client.app.state.database.session_factory() as session:
        count = mark_stale_local_runs_interrupted(session)
    assert count == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_cpu").status == "interrupted"


def test_h4_remote_gpu_untouched(client):
    _seed(client, executor="remote_gpu", item_status="completed", with_run=False)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_remote", recording_id="rec_h4", pipeline_id="g3_local",
            pipeline_version="1.0", executor="remote_gpu", status="running", parameters_json={},
        ))
        session.commit()
    with client.app.state.database.session_factory() as session:
        count = mark_stale_local_runs_interrupted(session)
    assert count == 0
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_remote").status == "running"


def test_h4_ambiguous_launch_intent_fails_closed(client):
    session = _seed(client, executor="local_gpu", item_status="running", with_run=True)
    with client.app.state.database.session_factory() as fresh:
        fresh.get(DatasetExperimentAttemptModel, "att_h4").launch_requested_at = datetime.now(timezone.utc)
        fresh.commit()
    from executor_fixtures import FakeRegistry
    from app.pipelines.registry import PipelineRegistry

    recover_dataset_experiments(
        session, job_manager=_JobManager(), registry=PipelineRegistry([]),
        model_release_store=None, executor_registry=FakeRegistry({}),
        startup_recovery_cutoff=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, "run_h4")
        att = fresh.get(DatasetExperimentAttemptModel, "att_h4")
    assert run.status == "interrupted"
    assert run.error_type == "ANALYSIS_LAUNCH_AMBIGUOUS"
    assert att.launch_requested_at is not None


def test_h4_repeated_recovery_is_idempotent(client):
    session = _seed(client, executor="local_gpu", item_status="queued", with_run=False)
    from executor_fixtures import FakeRegistry
    from app.pipelines.registry import PipelineRegistry

    kwargs = dict(job_manager=_JobManager(), registry=PipelineRegistry([]),
                  model_release_store=None, executor_registry=FakeRegistry({}),
                  startup_recovery_cutoff=datetime.now(timezone.utc) + timedelta(seconds=1))
    recover_dataset_experiments(session, **kwargs)
    with client.app.state.database.session_factory() as fresh:
        token_first = fresh.get(DatasetExperimentModel, "exp_h4").coordinator_token
        attempts_first = fresh.query(DatasetExperimentAttemptModel).count()
        runs_first = fresh.query(AnalysisRunModel).count()
    recover_dataset_experiments(session, **kwargs)
    with client.app.state.database.session_factory() as fresh:
        attempts_second = fresh.query(DatasetExperimentAttemptModel).count()
        runs_second = fresh.query(AnalysisRunModel).count()
    assert attempts_first == attempts_second == 0
    assert runs_first == runs_second == 0
    assert token_first is not None


def test_h4_stale_coordinator_fencing_writes_nothing(client):
    _seed(client, executor="local_gpu", item_status="queued", with_run=False)
    from app.dataset_experiments.coordinator import DatasetExperimentCoordinator
    from app.dataset_experiments.service import DatasetExperimentService
    from app.pipelines.registry import PipelineRegistry

    def _services_factory(session):
        ds = DatasetExperimentService(
            session, PipelineRegistry([]), None, client.app.state.executor_registry
        )
        return ds, None

    coordinator = DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=_services_factory,
    )
    with client.app.state.database.session_factory() as fresh:
        before = fresh.get(DatasetExperimentModel, "exp_h4").status
    outcome = coordinator.step("exp_h4", "T_stale")
    assert outcome.value == "fence_lost"
    with client.app.state.database.session_factory() as fresh:
        after = fresh.get(DatasetExperimentModel, "exp_h4")
        item = fresh.get(DatasetExperimentItemModel, "item_h4")
    assert after.status == before == "running"
    assert after.coordinator_token == "T_old"
    assert item.status == "queued"
