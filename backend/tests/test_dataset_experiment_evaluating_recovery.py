import pytest

from dataset_experiment_fixtures import (
    G3LocalPipeline,
    FakeProvider,
    FakeRegistry,
    item,
    mark_item_completed_with_run,
    running_experiment_with_token,
)

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments import recovery as rec
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.pipelines.registry import PipelineRegistry


class RecordingRecoveryJobManager:
    def __init__(self, *, fail=False, pid=4242):
        self.calls = []
        self.fail = fail
        self.pid = pid

    def start(self, experiment_id, coordinator_token):
        self.calls.append((experiment_id, coordinator_token))
        if self.fail:
            raise OSError("cannot spawn coordinator")
        return self.pid


def _deps(provider):
    return PipelineRegistry([G3LocalPipeline()]), FakeRegistry({"local_cpu": provider})


def _evaluating_experiment(client, *, evaluation_status="pending", worker_pid=None, count=2):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=count)
    for order in range(count):
        target = item(session, experiment.id, order=order)
        mark_item_completed_with_run(
            session, item=target, run_id=f"run_done_{order}", attempt_id=f"att_done_{order}"
        )
    bench = DatasetBenchmarkService(session)
    preview = bench.prepare_manifest(
        experiment.dataset_name, experiment.dataset_split, experiment.dataset_label_space
    )
    membership = [
        {"recording_id": item(session, experiment.id, order=o).recording_id,
         "analysis_run_id": f"run_done_{o}"}
        for o in range(count)
    ]
    evaluation = bench.prepare_evaluation(
        name="ev", dataset_name=experiment.dataset_name,
        dataset_split=experiment.dataset_split,
        label_space=experiment.dataset_label_space,
        recording_manifest_hash=preview.recording_manifest_hash,
        items=membership, allow_incomplete=False,
    )
    session.flush()
    experiment.dataset_evaluation_id = evaluation.id
    experiment.status = "evaluating"
    evaluation.status = evaluation_status
    evaluation.worker_pid = worker_pid
    session.commit()
    return session, ds, experiment, evaluation, provider


def _recover(client, provider, *, cutoff, job_manager):
    registry, executor_registry = _deps(provider)
    with client.app.state.database.session_factory() as session:
        return rec.recover_dataset_experiments(
            session, job_manager=job_manager, registry=registry,
            model_release_store=None, executor_registry=executor_registry,
            startup_recovery_cutoff=cutoff,
        )


def _counts(client):
    with client.app.state.database.session_factory() as fresh:
        return (
            fresh.query(DatasetExperimentAttemptModel).count(),
            fresh.query(AnalysisRunModel).count(),
        )


def test_evaluating_experiment_receives_fresh_generation_and_coordinator(client):
    session, ds, experiment, evaluation, provider = _evaluating_experiment(client)
    job_manager = RecordingRecoveryJobManager()
    report = _recover(client, provider, cutoff=rec._now(), job_manager=job_manager)
    assert report.claimed == 1
    assert report.coordinators_started == 1
    assert len(job_manager.calls) == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.coordinator_token != "T"
        assert stored.status == "evaluating"
        assert stored.worker_pid == 4242


def test_evaluating_recovery_creates_zero_analysis_runs_and_no_repair(client):
    session, ds, experiment, evaluation, provider = _evaluating_experiment(client)
    before = _counts(client)
    _recover(client, provider, cutoff=rec._now(), job_manager=RecordingRecoveryJobManager())
    assert _counts(client) == before
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "pending"


def test_same_startup_cutoff_protects_evaluating(client):
    session, ds, experiment, evaluation, provider = _evaluating_experiment(client)
    cutoff = rec._now()
    report_a = _recover(client, provider, cutoff=cutoff, job_manager=RecordingRecoveryJobManager())
    assert report_a.coordinators_started == 1
    job_b = RecordingRecoveryJobManager()
    report_b = _recover(client, provider, cutoff=cutoff, job_manager=job_b)
    assert report_b.skipped == 1
    assert job_b.calls == []


def test_stale_evaluating_coordinator_fenced(client):
    session, ds, experiment, evaluation, provider = _evaluating_experiment(client)
    _recover(client, provider, cutoff=rec._now(), job_manager=RecordingRecoveryJobManager())
    from dataset_experiment_fixtures import step_once

    assert step_once(client, experiment.id, "T").value == "fence_lost"


def test_pending_with_pid_normalized_to_interrupted(client):
    session, ds, experiment, evaluation, provider = _evaluating_experiment(
        client, evaluation_status="pending", worker_pid=999
    )
    _recover(client, provider, cutoff=rec._now(), job_manager=RecordingRecoveryJobManager())
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetEvaluationModel, evaluation.id)
        assert stored.status == "interrupted"
        assert stored.error_type == "BENCHMARK_INTERRUPTED"


def test_stale_recovery_cannot_normalize_after_generation_rotation(client):
    session, ds, experiment, evaluation, provider = _evaluating_experiment(
        client, evaluation_status="pending", worker_pid=999
    )
    with pytest.raises(PlatformError) as exc:
        rec.normalize_orphaned_evaluation(
            session, experiment.id, evaluation.id, "STALE_TOKEN"
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "pending"


def test_running_experiment_recovery_unchanged(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    job_manager = RecordingRecoveryJobManager()
    report = _recover(client, provider, cutoff=rec._now(), job_manager=job_manager)
    assert report.claimed == 1
    assert report.coordinators_started == 1
