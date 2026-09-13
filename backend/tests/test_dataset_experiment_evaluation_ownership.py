from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from dataset_experiment_fixtures import (
    G3LocalPipeline,
    FakeRegistry,
    create_experiment,
    item,
    local_services,
    mark_item_completed_with_run,
    running_experiment_with_token,
    seed_dataset,
)

from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.registry import PipelineRegistry


class RecordingExperimentJobManager:
    """Benchmark job manager used by automatic linked evaluation start."""

    def __init__(self, *, on_start=None, fail=False, pid=4242):
        self.calls = []
        self.on_start = on_start
        self.fail = fail
        self.pid = pid

    def start(self, evaluation_id):
        self.calls.append(evaluation_id)
        if self.on_start is not None:
            self.on_start(evaluation_id)
        if self.fail:
            raise OSError("cannot spawn benchmark worker")
        return self.pid


def _completed_experiment_with_evaluation(client, *, count=2, status="evaluating"):
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
    experiment.status = status
    session.commit()
    return session, ds, experiment, evaluation, provider


def _fresh(client):
    return client.app.state.database.session_factory()


def test_evaluating_heartbeat_succeeds_current_token(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    assert ds.refresh_coordinator_heartbeat(experiment.id, "T", expected_status="evaluating") is True
    with _fresh(client) as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).heartbeat_at is not None


def test_evaluating_heartbeat_stale_token_fence_lost(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    assert ds.refresh_coordinator_heartbeat(experiment.id, "STALE", expected_status="evaluating") is False


def test_evaluating_failure_projection_current_token(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    assert ds._fail_experiment(experiment.id, "T", "X", "boom", expected_status="evaluating") is True
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "X"


def test_evaluating_failure_projection_stale_token_noop(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    assert ds._fail_experiment(experiment.id, "STALE", "X", "boom", expected_status="evaluating") is False
    with _fresh(client) as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "evaluating"


def test_running_heartbeat_default_behavior_unchanged(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    assert ds.refresh_coordinator_heartbeat(experiment.id, "T") is True


def test_mark_experiment_completed_guards_token_and_status(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    assert ds.mark_experiment_completed(experiment.id, "STALE") is False
    assert ds.mark_experiment_completed(experiment.id, "T") is True
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "completed"
        assert stored.completed_at is not None


def test_link_evaluation_cas_sets_link_and_evaluating_once(client):
    seed_dataset(client, count=1)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    experiment.status = "running"
    experiment.coordinator_token = "T"
    session.commit()
    assert ds.link_evaluation(experiment.id, "eval_x", "T") is True
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "evaluating"
        assert stored.dataset_evaluation_id == "eval_x"
    assert ds.link_evaluation(experiment.id, "eval_y", "T") is False


def test_link_evaluation_stale_token_returns_false(client):
    seed_dataset(client, count=1)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    experiment.status = "running"
    experiment.coordinator_token = "T"
    session.commit()
    assert ds.link_evaluation(experiment.id, "eval_x", "STALE") is False
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.dataset_evaluation_id is None


def test_build_evaluation_membership_manifest_order_and_success_runs(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client, count=2)
    membership = ds.build_evaluation_membership(experiment.id)
    assert [m["analysis_run_id"] for m in membership] == ["run_done_0", "run_done_1"]


def test_validate_evaluation_linkage_accepts_valid_link(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    exp, ev = ds.validate_evaluation_linkage(experiment.id)
    assert exp.id == experiment.id
    assert ev.id == evaluation.id


def test_validate_evaluation_linkage_rejects_manifest_mismatch(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    evaluation.recording_manifest_hash = "c" * 64
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.validate_evaluation_linkage(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_validate_evaluation_linkage_rejects_manifest_order_mismatch(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    eval_items = list(session.scalars(
        select(DatasetEvaluationItemModel)
        .where(DatasetEvaluationItemModel.evaluation_id == evaluation.id)
        .order_by(DatasetEvaluationItemModel.manifest_order)
    ).all())
    eval_items[0].manifest_order = 100
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.validate_evaluation_linkage(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_validate_evaluation_linkage_rejects_item_status_corruption(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    eval_item = session.scalars(
        select(DatasetEvaluationItemModel)
        .where(DatasetEvaluationItemModel.evaluation_id == evaluation.id)
    ).first()
    eval_item.status = "missing_run"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.validate_evaluation_linkage(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_validate_evaluation_linkage_rejects_protocol_mismatch(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    evaluation.evaluation_protocol = "physical_tf_detection_ap_v1"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.validate_evaluation_linkage(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_validate_evaluation_linkage_rejects_membership_mismatch(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    eval_item = session.scalars(
        select(DatasetEvaluationItemModel)
        .where(DatasetEvaluationItemModel.evaluation_id == evaluation.id)
        .order_by(DatasetEvaluationItemModel.manifest_order)
    ).first()
    eval_item.analysis_run_id = None
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.validate_evaluation_linkage(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_reset_interrupted_evaluation_current_generation(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    evaluation.status = "interrupted"
    session.commit()
    assert ds.reset_interrupted_evaluation(experiment.id, evaluation.id, "T") is True
    with _fresh(client) as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "pending"


def test_reset_interrupted_evaluation_stale_generation_fence_lost(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    evaluation.status = "interrupted"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.reset_interrupted_evaluation(experiment.id, evaluation.id, "STALE")
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with _fresh(client) as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "interrupted"


def test_reset_interrupted_evaluation_platform_error_rollback_session_reusable(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    # Evaluation is `completed`; prepare_retry_evaluation will reject it -> PlatformError.
    evaluation.status = "completed"
    session.commit()
    with pytest.raises(PlatformError):
        ds.reset_interrupted_evaluation(experiment.id, evaluation.id, "T")
    assert session.in_transaction() is False
    # Session remains usable.
    assert session.get(DatasetExperimentModel, experiment.id) is not None


def test_start_linked_evaluation_current_generation_spawns_once(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    job_manager = RecordingExperimentJobManager()
    assert ds.start_linked_evaluation(experiment.id, evaluation.id, "T", job_manager) == "started"
    assert len(job_manager.calls) == 1
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetEvaluationModel, evaluation.id)
        assert stored.status == "running"
        assert stored.worker_pid == 4242


def test_start_linked_evaluation_claim_committed_before_spawn(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    observed = {}

    def probe(evaluation_id):
        with _fresh(client) as fresh:
            stored = fresh.get(DatasetEvaluationModel, evaluation_id)
            observed["status"] = stored.status
            observed["started_at"] = stored.started_at
        observed["in_transaction"] = session.in_transaction()

    ds.start_linked_evaluation(
        experiment.id, evaluation.id, "T", RecordingExperimentJobManager(on_start=probe)
    )
    assert observed["status"] == "running"
    assert observed["started_at"] is not None
    assert observed["in_transaction"] is False


def test_start_linked_evaluation_same_token_two_actors_one_claim(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    session_b = _fresh(client)
    ds_b = DatasetExperimentService(
        session_b, PipelineRegistry([G3LocalPipeline()]), None,
        FakeRegistry({"local_cpu": provider}),
    )
    job_manager = RecordingExperimentJobManager()
    first = ds.start_linked_evaluation(experiment.id, evaluation.id, "T", job_manager)
    second = ds_b.start_linked_evaluation(experiment.id, evaluation.id, "T", job_manager)
    assert first == "started"
    assert second == "already_started"
    assert len(job_manager.calls) == 1


def test_start_linked_evaluation_stale_generation_zero_spawn(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    job_manager = RecordingExperimentJobManager()
    with pytest.raises(PlatformError) as exc:
        ds.start_linked_evaluation(experiment.id, evaluation.id, "STALE", job_manager)
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    assert job_manager.calls == []


def test_start_linked_evaluation_spawn_failure_write_fenced(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    with pytest.raises(PlatformError) as exc:
        ds.start_linked_evaluation(
            experiment.id, evaluation.id, "T", RecordingExperimentJobManager(fail=True)
        )
    assert exc.value.code == "DATASET_EXPERIMENT_EVALUATION_FAILED"
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetEvaluationModel, evaluation.id)
        assert stored.status == "failed"
        assert stored.error_type == "BENCHMARK_FAILED"


def test_start_linked_evaluation_pid_uncertainty_returns_uncertain(client, monkeypatch):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    real_commit = session.commit
    counter = {"n": 0}

    def flaky_commit():
        counter["n"] += 1
        if counter["n"] == 2:  # claim commit is 1; PID persist commit is 2
            raise RuntimeError("pid persist failed")
        real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)
    result = ds.start_linked_evaluation(
        experiment.id, evaluation.id, "T", RecordingExperimentJobManager()
    )
    assert result == "uncertain"
    with _fresh(client) as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "running"


def test_start_linked_evaluation_stale_after_spawn_zero_newer_mutation(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)

    def rotate(evaluation_id):
        with _fresh(client) as other:
            stored = other.get(DatasetExperimentModel, experiment.id)
            stored.coordinator_token = "T2"
            other.commit()

    with pytest.raises(PlatformError) as exc:
        ds.start_linked_evaluation(
            experiment.id, evaluation.id, "T",
            RecordingExperimentJobManager(on_start=rotate),
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with _fresh(client) as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).coordinator_token == "T2"


class RecordingCoordinatorJobManager:
    """DatasetExperiment coordinator job manager (start(experiment_id, token))."""

    def __init__(self, *, on_start=None, fail=False, pid=4242):
        self.calls = []
        self.on_start = on_start
        self.fail = fail
        self.pid = pid

    def start(self, experiment_id, coordinator_token):
        self.calls.append((experiment_id, coordinator_token))
        if self.on_start is not None:
            self.on_start(experiment_id, coordinator_token)
        if self.fail:
            raise OSError("cannot spawn coordinator")
        return self.pid


def _failed_experiment_with_failed_evaluation(client, *, count=2, evaluation_status="failed"):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(
        client, count=count, status="failed"
    )
    evaluation.status = evaluation_status
    session.commit()
    return session, ds, experiment, evaluation, provider


def test_retry_evaluation_requires_failed_experiment(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    job_manager = RecordingCoordinatorJobManager()
    with pytest.raises(PlatformError) as exc:
        ds.retry_evaluation(experiment.id, job_manager)
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
    assert job_manager.calls == []


def test_retry_evaluation_rejects_incomplete_inference(client):
    session, ds, experiment, evaluation, provider = _failed_experiment_with_failed_evaluation(client)
    target = session.scalars(
        select(DatasetExperimentItemModel)
        .where(DatasetExperimentItemModel.experiment_id == experiment.id)
    ).first()
    target.status = "running"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        ds.retry_evaluation(experiment.id, RecordingCoordinatorJobManager())
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"


def test_retry_evaluation_resets_and_spawns_zero_new_attempts_or_runs(client):
    session, ds, experiment, evaluation, provider = _failed_experiment_with_failed_evaluation(client)
    job_manager = RecordingCoordinatorJobManager()
    returned = ds.retry_evaluation(experiment.id, job_manager)
    assert returned.status == "evaluating"
    assert len(job_manager.calls) == 1
    with _fresh(client) as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "pending"
        assert fresh.query(DatasetExperimentAttemptModel).count() == 2  # unchanged
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "evaluating"


def test_retry_evaluation_spawn_failure_restores_both_projections(client):
    session, ds, experiment, evaluation, provider = _failed_experiment_with_failed_evaluation(client)
    with pytest.raises(PlatformError) as exc:
        ds.retry_evaluation(experiment.id, RecordingCoordinatorJobManager(fail=True))
    assert exc.value.code == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"
    with _fresh(client) as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "failed"


def test_retry_evaluation_stale_compensation_zero_mutation(client):
    session, ds, experiment, evaluation, provider = _failed_experiment_with_failed_evaluation(client)

    def rotate(experiment_id, token):
        with _fresh(client) as other:
            stored = other.get(DatasetExperimentModel, experiment_id)
            stored.coordinator_token = "T2"
            other.commit()

    with pytest.raises(PlatformError) as exc:
        ds.retry_evaluation(
            experiment.id, RecordingCoordinatorJobManager(on_start=rotate, fail=True)
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with _fresh(client) as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).coordinator_token == "T2"
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "pending"


def test_retry_evaluation_concurrent_one_winner(client):
    session, ds, experiment, evaluation, provider = _failed_experiment_with_failed_evaluation(client)
    session_b = _fresh(client)
    ds_b = DatasetExperimentService(
        session_b, PipelineRegistry([G3LocalPipeline()]), None,
        FakeRegistry({"local_cpu": provider}),
    )
    winner = RecordingCoordinatorJobManager(pid=1111)
    ds.retry_evaluation(experiment.id, winner)
    loser = RecordingCoordinatorJobManager(pid=2222)
    with pytest.raises(PlatformError) as exc:
        ds_b.retry_evaluation(experiment.id, loser)
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
    assert loser.calls == []


def test_retry_evaluation_pid_cas_loss_returns_fence_lost(client):
    session, ds, experiment, evaluation, provider = _failed_experiment_with_failed_evaluation(client)

    def rotate(experiment_id, token):
        with _fresh(client) as other:
            stored = other.get(DatasetExperimentModel, experiment_id)
            stored.coordinator_token = "T2"
            other.commit()

    with pytest.raises(PlatformError) as exc:
        ds.retry_evaluation(
            experiment.id, RecordingCoordinatorJobManager(on_start=rotate)
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"


def test_manual_vs_automatic_start_one_claim(client):
    session, ds, experiment, evaluation, provider = _completed_experiment_with_evaluation(client)
    manual_manager = RecordingExperimentJobManager()
    DatasetBenchmarkService(session).start_evaluation(evaluation.id, manual_manager)
    assert len(manual_manager.calls) == 1

    automatic_manager = RecordingExperimentJobManager()
    result = ds.start_linked_evaluation(
        experiment.id, evaluation.id, "T", automatic_manager
    )
    assert result == "already_started"
    assert automatic_manager.calls == []
    with _fresh(client) as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation.id).status == "running"
