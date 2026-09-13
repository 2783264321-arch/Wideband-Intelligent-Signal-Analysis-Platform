import pytest

from dataset_experiment_fixtures import (
    item,
    mark_item_running_with_active_run,
    rotate_token,
    running_experiment_with_token,
    step_once,
)

from app.analysis.model import AnalysisRunModel
from app.dataset_experiments.coordinator import (
    CoordinatorOutcome,
    DatasetExperimentCoordinator,
)
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService


def test_matching_token_updates_heartbeat(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=1)
    target = item(session, experiment.id)
    mark_item_running_with_active_run(session, item=target)

    outcome = step_once(client, experiment.id, "T")
    assert outcome in {CoordinatorOutcome.WAITING, CoordinatorOutcome.SCHEDULED}
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).heartbeat_at is not None


def test_stale_token_returns_fence_lost_without_writes(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T2", max_concurrency=1)
    target = item(session, experiment.id)
    mark_item_running_with_active_run(session, item=target)
    before = session.get(DatasetExperimentModel, experiment.id).heartbeat_at

    outcome = step_once(client, experiment.id, "T_stale")
    assert outcome == CoordinatorOutcome.FENCE_LOST
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.heartbeat_at == before
        assert stored.status == "running"


def test_non_running_experiment_returns_terminal(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=1)
    experiment.status = "completed_with_failures"
    session.commit()
    assert step_once(client, experiment.id, "T") == CoordinatorOutcome.EXPERIMENT_TERMINAL


def test_rotation_before_transaction_a_creates_no_run_or_attempt(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=1)

    def rotate_then_prepare(real_prepare, **kwargs):
        rotate_token(client, experiment.id, "T2")
        return real_prepare(**kwargs)

    outcome = step_once(client, experiment.id, "T", prepare_wrapper=rotate_then_prepare)
    assert outcome == CoordinatorOutcome.FENCE_LOST
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
        assert fresh.query(DatasetExperimentItemModel).filter_by(status="queued").count() == 3


def test_rotation_before_reconciliation_commit_blocks_projection(client, monkeypatch):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", max_concurrency=1)
    target = item(session, experiment.id)
    mark_item_running_with_active_run(session, item=target)
    run = session.get(AnalysisRunModel, "run_active")
    run.status = "completed"
    session.commit()

    real_reconcile = DatasetExperimentService.reconcile_items

    def rotate_then_reconcile(self, experiment_id, coordinator_token=None):
        rotate_token(client, experiment_id, "T2")
        return real_reconcile(self, experiment_id, coordinator_token=coordinator_token)

    monkeypatch.setattr(DatasetExperimentService, "reconcile_items", rotate_then_reconcile)
    outcome = step_once(client, experiment.id, "T")
    assert outcome == CoordinatorOutcome.FENCE_LOST
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, target.id).status == "running"
