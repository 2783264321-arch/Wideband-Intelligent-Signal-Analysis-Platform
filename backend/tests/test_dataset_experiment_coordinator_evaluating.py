import pytest

from dataset_experiment_fixtures import (
    item,
    mark_item_completed_with_run,
    running_experiment_with_token,
    services_factory_for,
)

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments.coordinator import CoordinatorOutcome, DatasetExperimentCoordinator
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService


class RecordingBenchmarkJobManager:
    def __init__(self, *, fail=False, pid=4242):
        self.calls = []
        self.fail = fail
        self.pid = pid

    def start(self, evaluation_id):
        self.calls.append(evaluation_id)
        if self.fail:
            raise OSError("cannot spawn benchmark worker")
        return self.pid


def _coordinator(client, *, provider, job_manager):
    return DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=services_factory_for(client, provider=provider),
        benchmark_services_factory=lambda session: DatasetBenchmarkService(session),
        benchmark_job_manager=job_manager,
    )


def _all_success_experiment(client, *, count=2):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=count)
    for order in range(count):
        target = item(session, experiment.id, order=order)
        mark_item_completed_with_run(
            session, item=target, run_id=f"run_done_{order}", attempt_id=f"att_done_{order}"
        )
    return session, ds, analysis, provider, experiment


def _counts(client):
    with client.app.state.database.session_factory() as fresh:
        return (
            fresh.query(DatasetEvaluationModel).count(),
            fresh.query(DatasetExperimentAttemptModel).count(),
            fresh.query(AnalysisRunModel).count(),
        )


def test_all_success_creates_exactly_one_linked_evaluation(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.WAITING
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "evaluating"
        assert stored.dataset_evaluation_id is not None
        evaluation = fresh.get(DatasetEvaluationModel, stored.dataset_evaluation_id)
        assert evaluation.missing_recordings == 0
        assert evaluation.expected_recordings == 2
        assert evaluation.coverage == 1.0
        assert fresh.query(DatasetEvaluationItemModel).filter_by(
            evaluation_id=evaluation.id).count() == 2
    assert len(job_manager.calls) == 1


def test_all_success_without_benchmark_wiring_returns_inference_complete(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    coordinator = DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=services_factory_for(client, provider=provider),
    )
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.INFERENCE_COMPLETE
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "running"
        assert fresh.query(DatasetEvaluationModel).count() == 0


def test_repeated_step_creates_no_duplicate(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    coordinator.step(experiment.id, "T")
    coordinator.step(experiment.id, "T")
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(DatasetEvaluationModel).count() == 1


def test_evaluation_pending_without_pid_is_started_once(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    coordinator.step(experiment.id, "T")  # links + starts
    assert len(job_manager.calls) == 1
    coordinator.step(experiment.id, "T")  # evaluation now running -> wait
    assert len(job_manager.calls) == 1


def test_evaluation_completed_closes_experiment_same_iteration(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    coordinator.step(experiment.id, "T")
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        evaluation = fresh.get(DatasetEvaluationModel, stored.dataset_evaluation_id)
        evaluation.status = "completed"
        fresh.commit()
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.EXPERIMENT_COMPLETED
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "completed"
        assert stored.completed_at is not None


def test_evaluation_failed_fails_experiment(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    coordinator.step(experiment.id, "T")
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        evaluation = fresh.get(DatasetEvaluationModel, stored.dataset_evaluation_id)
        evaluation.status = "failed"
        evaluation.error_message = "metrics boom"
        fresh.commit()
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_evaluation_interrupted_retries_metrics_only(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    coordinator.step(experiment.id, "T")
    before = _counts(client)
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        evaluation = fresh.get(DatasetEvaluationModel, stored.dataset_evaluation_id)
        evaluation.status = "interrupted"
        fresh.commit()
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.WAITING
    after = _counts(client)
    assert after == before  # zero Attempts/Runs; no new evaluation
    assert len(job_manager.calls) == 1  # reset staged; not re-started in this step


def test_missing_linked_evaluation_fails_closed(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    experiment.dataset_evaluation_id = "eval_missing"
    experiment.status = "evaluating"
    session.commit()
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_every_evaluating_path_creates_zero_analysis_runs(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    coordinator.step(experiment.id, "T")
    runs_before = _counts(client)[2]
    coordinator.step(experiment.id, "T")
    assert _counts(client)[2] == runs_before


def test_partial_success_never_creates_evaluation(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=2)
    target = item(session, experiment.id, order=0)
    mark_item_completed_with_run(session, item=target, run_id="run_done_0", attempt_id="att_done_0")
    with client.app.state.database.session_factory() as fresh:
        other = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=1).one()
        other.status = "failed"
        fresh.commit()
    job_manager = RecordingBenchmarkJobManager()
    coordinator = _coordinator(client, provider=provider, job_manager=job_manager)
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.COMPLETED_WITH_FAILURES
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(DatasetEvaluationModel).count() == 0


def test_post_link_invariant_error_fails_experiment_under_evaluating_guard(client, monkeypatch):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    real = DatasetExperimentService.start_linked_evaluation

    def boom(self, *args, **kwargs):
        raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION", "boom", 409)

    monkeypatch.setattr(DatasetExperimentService, "start_linked_evaluation", boom)
    coordinator = _coordinator(client, provider=provider, job_manager=RecordingBenchmarkJobManager())
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_benchmark_start_failure_after_link_fails_evaluating_experiment(client):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)
    coordinator = _coordinator(
        client, provider=provider, job_manager=RecordingBenchmarkJobManager(fail=True)
    )
    outcome = coordinator.step(experiment.id, "T")
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "DATASET_EXPERIMENT_EVALUATION_FAILED"
