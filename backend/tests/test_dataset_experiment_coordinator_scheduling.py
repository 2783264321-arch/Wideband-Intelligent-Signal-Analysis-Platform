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


def test_experiment_level_execution_capability_failure_stops(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=2)
    outcome = step_once(client, experiment.id, "T",
                        prepare_wrapper=_raise("EXECUTION_CAPABILITY_UNAVAILABLE"))
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "EXECUTION_CAPABILITY_UNAVAILABLE"


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
