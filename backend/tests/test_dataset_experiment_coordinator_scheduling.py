import inspect

import pytest

from dataset_experiment_fixtures import (
    item,
    mark_item_running_with_active_run,
    running_experiment_with_token,
    step_once,
)

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.dataset_experiments import coordinator as coordinator_module
from app.dataset_experiments.coordinator import CoordinatorOutcome
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)

from executor_fixtures import FakeProvider


def _raise(code):
    def wrapper(real_prepare, **kwargs):
        raise PlatformError(code, "injected")
    return wrapper


def test_max_concurrency_one_starts_one(client):
    provider = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, provider=provider)
    outcome = step_once(client, experiment.id, "T", provider=provider)
    assert outcome == CoordinatorOutcome.SCHEDULED
    assert len(provider.launches) == 1
    with client.app.state.database.session_factory() as fresh:
        statuses = [
            row.status for row in fresh.query(DatasetExperimentItemModel)
            .filter_by(experiment_id=experiment.id).order_by(DatasetExperimentItemModel.manifest_order).all()
        ]
    assert statuses == ["running", "queued", "queued"]


def test_max_concurrency_two_starts_two(client):
    provider = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=2, provider=provider)
    outcome = step_once(client, experiment.id, "T", provider=provider)
    assert outcome == CoordinatorOutcome.SCHEDULED
    assert len(provider.launches) == 2
    with client.app.state.database.session_factory() as fresh:
        statuses = [
            row.status for row in fresh.query(DatasetExperimentItemModel)
            .filter_by(experiment_id=experiment.id).order_by(DatasetExperimentItemModel.manifest_order).all()
        ]
    assert statuses == ["running", "running", "queued"]


def test_existing_running_items_consume_slots(client):
    provider = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, provider=provider)
    target = item(session, experiment.id, order=0)
    mark_item_running_with_active_run(session, item=target)

    outcome = step_once(client, experiment.id, "T", provider=provider)
    assert outcome == CoordinatorOutcome.WAITING
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(DatasetExperimentItemModel).filter_by(status="queued").count() == 2


def test_never_oversubscribes(client):
    provider = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, provider=provider)
    target = item(session, experiment.id, order=0)
    mark_item_running_with_active_run(session, item=target)
    step_once(client, experiment.id, "T", provider=provider)
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="running"
        ).count() == 1
    assert provider.launches == []


def test_queued_items_started_in_manifest_order(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=3)
    order = []

    def record_order(real_prepare, **kwargs):
        order.append(kwargs["recording_id"])
        return real_prepare(**kwargs)

    outcome = step_once(client, experiment.id, "T", prepare_wrapper=record_order)
    assert outcome == CoordinatorOutcome.SCHEDULED
    assert order == ["rec_0", "rec_1", "rec_2"]


def test_item_level_failure_marks_item_failed_and_continues(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)

    def fail_first(real_prepare, **kwargs):
        if kwargs["recording_id"] == "rec_0":
            raise PlatformError("INPUT_INCOMPATIBLE", "injected")
        return real_prepare(**kwargs)

    outcome = step_once(client, experiment.id, "T", prepare_wrapper=fail_first)
    assert outcome == CoordinatorOutcome.SCHEDULED
    with client.app.state.database.session_factory() as fresh:
        first = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=0
        ).one()
        second = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=1
        ).one()
        assert first.status == "failed"
        assert first.last_error_type == "INPUT_INCOMPATIBLE"
        assert second.status == "running"


def test_execution_capability_unavailable_is_item_level(client):
    # Plan A1: EXECUTION_CAPABILITY_UNAVAILABLE at scheduling is a recoverable
    # deployment condition -> the item fails, the experiment is NOT failed.
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)
    outcome = step_once(client, experiment.id, "T",
                        prepare_wrapper=_raise("EXECUTION_CAPABILITY_UNAVAILABLE"))
    assert outcome in (CoordinatorOutcome.SCHEDULED, CoordinatorOutcome.WAITING)
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        failed = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, status="failed").count()
        assert failed >= 1


def test_remote_runtime_drift_stops_experiment(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)
    outcome = step_once(client, experiment.id, "T",
                        prepare_wrapper=_raise("REMOTE_IMPLEMENTATION_MISMATCH"))
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "REMOTE_IMPLEMENTATION_MISMATCH"


def test_remote_asset_drift_stops_experiment(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)
    outcome = step_once(client, experiment.id, "T",
                        prepare_wrapper=_raise("PIPELINE_ASSET_MISMATCH"))
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_impossible_launch_state_stops(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)
    outcome = step_once(client, experiment.id, "T",
                        prepare_wrapper=_raise("ANALYSIS_RUN_NOT_LAUNCHABLE"))
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_unknown_platform_error_fails_closed(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)
    outcome = step_once(client, experiment.id, "T",
                        prepare_wrapper=_raise("TOTALLY_UNKNOWN_CODE"))
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_scheduling_uses_g3a_then_g3b(client):
    provider = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, provider=provider)
    step_once(client, experiment.id, "T", provider=provider)
    assert len(provider.launches) == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 1
        assert fresh.query(DatasetExperimentAttemptModel).count() == 1


def test_no_direct_prepare_run_or_provider_launch():
    source = inspect.getsource(coordinator_module)
    assert "prepare_run(" not in source
    assert "provider.launch(" not in source


def test_authority_codes_are_item_level_classification():
    from app.dataset_experiments.coordinator import is_experiment_level
    assert not is_experiment_level(PlatformError("EXECUTION_CAPABILITY_UNAVAILABLE", "x"))
    assert not is_experiment_level(PlatformError("EXECUTION_NOT_CERTIFIED", "x"))
    assert is_experiment_level(PlatformError("RUNTIME_DESCRIPTOR_INVALID", "x"))
    assert is_experiment_level(PlatformError("DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED", "x"))


# ---------------------------------------------------------------------------
# Plan A1.1 — frozen runtime drift recovery (both timing windows).
# ---------------------------------------------------------------------------


def test_runtime_descriptor_drift_before_transaction_a_creates_no_run(client):
    """Window A (A1.1): runtime generation drifts BEFORE Transaction A.

    A DatasetExperiment is frozen to runtime generation A; the deployed provider
    is then replaced by an independently-valid generation B (same
    executor/device_type/device_index/precision, different environment identity).

    ``start_item_attempt`` must reject the mismatch before Transaction A, so no
    durable execution state is created at all: Experiment fails, the Item stays
    queued, and there is no Attempt, no AnalysisRun and no worker.
    """
    provider_a = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, count=1, provider=provider_a
    )

    # Generation B: independently valid, different environment identity.
    provider_b = FakeProvider("local_cpu", runtime_ref="fake:local_cpu:genB")

    outcome = step_once(client, experiment.id, "T", provider=provider_b)

    assert provider_b.launches == []
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED

    with client.app.state.database.session_factory() as fresh:
        stored_experiment = fresh.get(DatasetExperimentModel, experiment.id)
        stored_item = item(fresh, experiment.id, order=0)
        attempt_count = (
            fresh.query(DatasetExperimentAttemptModel)
            .filter_by(experiment_item_id=stored_item.id)
            .count()
        )
        run_count = fresh.query(AnalysisRunModel).count()

    assert stored_experiment.status == "failed"
    assert stored_item.status == "queued"   # never mutated to manufacture a failed item
    assert attempt_count == 0               # Transaction A never created
    assert run_count == 0                   # no pending Run ever staged


def test_runtime_descriptor_drift_after_transaction_a_terminalizes_owned_run(client):
    """Window B (A1.1): runtime generation drifts AFTER Transaction A.

    Transaction A commits under A (Item running, Attempt, Run pending), then the
    deployed provider is replaced by generation B. ``launch_item_attempt`` must
    detect ``RUNTIME_DESCRIPTOR_INVALID`` and — because that code is
    experiment-level and never marks the owned Item failed — clean up the owned
    Run and Item under the same generation fencing before re-raising.
    """
    provider_a = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, count=1, provider=provider_a
    )
    target = item(session, experiment.id, order=0)

    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=target.id,
        analysis_service=analysis, coordinator_token="T",
    )

    # Deploy generation B after Transaction A committed.
    provider_b = FakeProvider("local_cpu", runtime_ref="fake:local_cpu:genB")
    ds.executor_registry._providers["local_cpu"] = provider_b

    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id=target.id, attempt_id=attempt.id,
            analysis_service=analysis, coordinator_token="T",
        )
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"

    with client.app.state.database.session_factory() as fresh:
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        stored_item = fresh.get(DatasetExperimentItemModel, target.id)

    assert stored_attempt.launch_requested_at is None   # no launch intent claimed
    assert stored_run.status == "interrupted"           # owned Run terminalized
    assert stored_run.error_type == "RUNTIME_DESCRIPTOR_INVALID"
    assert stored_run.worker_pid is None
    assert stored_item.status == "failed"               # owned Item failed
    assert provider_b.launches == []                    # no worker launched


def test_window_b_cleanup_respects_generation_fencing(client):
    """A stale coordinator token must not perform the Window-B cleanup."""
    provider_a = FakeProvider("local_cpu")
    session, ds, analysis, _, experiment = running_experiment_with_token(
        client, "T", max_concurrency=1, count=1, provider=provider_a
    )
    target = item(session, experiment.id, order=0)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=target.id,
        analysis_service=analysis, coordinator_token="T",
    )

    provider_b = FakeProvider("local_cpu", runtime_ref="fake:local_cpu:genB")
    ds.executor_registry._providers["local_cpu"] = provider_b
    experiment.coordinator_token = "T2"  # generation rotated
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id=target.id, attempt_id=attempt.id,
            analysis_service=analysis, coordinator_token="T",  # stale
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"

    with client.app.state.database.session_factory() as fresh:
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        stored_item = fresh.get(DatasetExperimentItemModel, target.id)

    assert stored_run.status == "pending"               # untouched by stale actor
    assert stored_attempt.launch_requested_at is None
    assert stored_item.status == "running"              # untouched by stale actor
