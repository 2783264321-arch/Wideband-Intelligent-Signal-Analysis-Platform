from datetime import datetime, timezone

import pytest

from dataset_experiment_fixtures import (
    RecordingJobManager,
    completed_with_failures_experiment,
    local_services,
    step_once,
)

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.dataset_experiments.coordinator import CoordinatorOutcome
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)


def _failed_items(session, experiment_id):
    return list(
        session.query(DatasetExperimentItemModel)
        .filter_by(experiment_id=experiment_id, status="failed")
        .order_by(DatasetExperimentItemModel.manifest_order)
        .all()
    )


def _seed_failed_attempt(session, target, run_id, attempt_id):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=target.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="failed",
        parameters_json={}, error_type="ANALYSIS_FAILED", error_message="boom",
    ))
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=target.id, attempt_number=1,
        analysis_run_id=run_id, launch_requested_at=datetime.now(timezone.utc),
    ))
    session.commit()


def test_retry_requires_completed_with_failures(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)
    for status in ("pending", "running", "completed", "failed"):
        experiment.status = status
        session.commit()
        job_manager = RecordingJobManager()
        with pytest.raises(PlatformError) as exc:
            ds.retry_failed(experiment.id, job_manager)
        assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
        assert job_manager.calls == []
        experiment.status = "completed_with_failures"
        session.commit()


def test_retry_missing_experiment_is_not_found(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client)
    with pytest.raises(PlatformError) as exc:
        ds.retry_failed("missing", RecordingJobManager())
    assert exc.value.code == "DATASET_EXPERIMENT_NOT_FOUND"


def test_retry_no_failed_items_rejected(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=0, completed=2)
    job_manager = RecordingJobManager()
    with pytest.raises(PlatformError) as exc:
        ds.retry_failed(experiment.id, job_manager)
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
    assert job_manager.calls == []


def test_retry_requeues_only_failed_items_and_leaves_completed(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=2)
    ds.retry_failed(experiment.id, RecordingJobManager())
    with client.app.state.database.session_factory() as fresh:
        failed = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="failed").count()
        queued = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="queued").count()
        completed = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="completed").count()
    assert failed == 0
    assert queued == 1
    assert completed == 2


def test_retry_clears_item_error_projection(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)
    ds.retry_failed(experiment.id, RecordingJobManager())
    with client.app.state.database.session_factory() as fresh:
        target = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="queued").one()
        assert target.last_error_type is None
        assert target.last_error_message is None


def test_retry_clears_experiment_terminal_projection(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)
    started_at = experiment.started_at
    returned = ds.retry_failed(experiment.id, RecordingJobManager())
    assert returned.status == "running"
    assert returned.completed_at is None
    assert returned.error_type is None
    assert returned.error_message is None
    assert returned.started_at == started_at


def test_retry_preserves_old_attempts_and_runs(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)
    target = _failed_items(session, experiment.id)[0]
    _seed_failed_attempt(session, target, run_id="run_old", attempt_id="att_old")

    ds.retry_failed(experiment.id, RecordingJobManager())

    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_old").status == "failed"
        assert fresh.get(DatasetExperimentAttemptModel, "att_old") is not None
        assert fresh.query(DatasetExperimentAttemptModel).count() == 1
        assert fresh.query(AnalysisRunModel).count() == 1


def test_retry_creates_no_new_attempt_or_run(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=2, completed=1)
    ds.retry_failed(experiment.id, RecordingJobManager())
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
        assert fresh.query(AnalysisRunModel).count() == 0


def test_retry_rotates_token_and_persists_pid(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)
    job_manager = RecordingJobManager()
    returned = ds.retry_failed(experiment.id, job_manager)
    assert len(job_manager.calls) == 1
    spawned_id, spawned_token = job_manager.calls[0]
    assert spawned_id == experiment.id
    assert spawned_token == returned.coordinator_token
    assert spawned_token != "coord_old"
    assert returned.worker_pid == 4242


def test_retry_token_persisted_before_spawn(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)

    def probe(experiment_id, token):
        with client.app.state.database.session_factory() as fresh:
            stored = fresh.get(DatasetExperimentModel, experiment_id)
            assert stored.status == "running"
            assert stored.coordinator_token == token
            assert stored.worker_pid is None

    ds.retry_failed(experiment.id, RecordingJobManager(token_probe=probe))


def test_retry_old_generation_fenced(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)
    ds.retry_failed(experiment.id, RecordingJobManager())
    outcome = step_once(client, experiment.id, "coord_old")
    assert outcome == CoordinatorOutcome.FENCE_LOST
    with client.app.state.database.session_factory() as fresh:
        target = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="queued").one()
        assert target.status == "queued"


def test_retry_concurrent_two_session_one_winner(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)

    session_b, ds_b, _, provider_b = local_services(client)
    loaded_by_b = session_b.get(DatasetExperimentModel, experiment.id)
    assert loaded_by_b.status == "completed_with_failures"

    winner_manager = RecordingJobManager(pid=1111)
    ds.retry_failed(experiment.id, winner_manager)

    loser_manager = RecordingJobManager(pid=2222)
    with pytest.raises(PlatformError) as exc:
        ds_b.retry_failed(experiment.id, loser_manager)
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
    assert loser_manager.calls == []
    assert len(winner_manager.calls) == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.worker_pid == 1111


def test_retry_stale_session_loser_zero_spawn(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=1, completed=1)

    session_b, ds_b, _, provider_b = local_services(client)
    session_b.get(DatasetExperimentModel, experiment.id)  # B loads the pre-retry state

    ds.retry_failed(experiment.id, RecordingJobManager(pid=1111))

    loser_manager = RecordingJobManager()
    with pytest.raises(PlatformError):
        ds_b.retry_failed(experiment.id, loser_manager)
    assert loser_manager.calls == []


def test_retry_spawn_failure_restores_terminal_projection(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=2, completed=1)
    snapshot = {
        "completed_at": experiment.completed_at,
        "heartbeat_at": experiment.heartbeat_at,
        "coordinator_token": experiment.coordinator_token,
        "worker_pid": experiment.worker_pid,
        "error_type": experiment.error_type,
        "error_message": experiment.error_message,
    }

    with pytest.raises(PlatformError) as exc:
        ds.retry_failed(experiment.id, RecordingJobManager(fail=True))
    assert exc.value.code == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"

    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "completed_with_failures"
        assert stored.completed_at == snapshot["completed_at"]
        assert stored.heartbeat_at == snapshot["heartbeat_at"]
        assert stored.coordinator_token == snapshot["coordinator_token"]
        assert stored.worker_pid == snapshot["worker_pid"]
        assert stored.error_type == snapshot["error_type"]
        assert stored.error_message == snapshot["error_message"]
        failed = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="failed").all()
        assert len(failed) == 2
        for target in failed:
            assert target.last_error_type == "ANALYSIS_FAILED"
            assert target.last_error_message == "boom"
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
        assert fresh.query(AnalysisRunModel).count() == 0


def test_retry_compensation_loses_generation_zero_item_mutation(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=2, completed=0)

    def takeover(experiment_id, token):
        with client.app.state.database.session_factory() as other:
            stored = other.get(DatasetExperimentModel, experiment_id)
            stored.coordinator_token = "coord_T2"
            stored.heartbeat_at = datetime.now(timezone.utc)
            other.commit()

    job_manager = RecordingJobManager(on_start=takeover, fail=True)
    with pytest.raises(PlatformError) as exc:
        ds.retry_failed(experiment.id, job_manager)
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"

    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.coordinator_token == "coord_T2"
        queued = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="queued").count()
        failed = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="failed").count()
    assert queued == 2
    assert failed == 0


def test_retry_compensation_ownership_cas_required_before_item_restore(client):
    session, ds, experiment, provider = completed_with_failures_experiment(client, failed=2, completed=0)
    targets = _failed_items(session, experiment.id)
    item_snapshot = [(t.id, t.last_error_type, t.last_error_message) for t in targets]
    terminal_snapshot = {
        "completed_at": experiment.completed_at,
        "heartbeat_at": experiment.heartbeat_at,
        "coordinator_token": experiment.coordinator_token,
        "worker_pid": experiment.worker_pid,
        "error_type": experiment.error_type,
        "error_message": experiment.error_message,
    }
    # Simulate the post-Transaction-1 state: Items requeued, retry token live.
    for target in targets:
        target.status = "queued"
    experiment.status = "running"
    experiment.coordinator_token = "coord_T_retry"
    session.commit()

    restored = ds._restore_retry_failed(
        experiment.id, "coord_WRONG", item_snapshot, terminal_snapshot
    )
    assert restored is False
    with client.app.state.database.session_factory() as fresh:
        queued = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="queued").count()
        stored = fresh.get(DatasetExperimentModel, experiment.id)
    assert queued == 2
    assert stored.coordinator_token == "coord_T_retry"
    assert stored.status == "running"
