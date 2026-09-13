from dataset_experiment_fixtures import (
    item,
    mark_item_completed_with_run,
    running_experiment_with_token,
    step_once,
)

from app.benchmarks.model import DatasetEvaluationModel
from app.dataset_experiments.coordinator import CoordinatorOutcome
from app.dataset_experiments.model import (
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)


def test_completed_with_failures_sets_status_and_exits(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=3)
    for target in session.query(DatasetExperimentItemModel).filter_by(experiment_id=experiment.id).all():
        target.status = "failed"
    session.commit()

    outcome = step_once(client, experiment.id, "T")
    assert outcome == CoordinatorOutcome.COMPLETED_WITH_FAILURES
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "completed_with_failures"
        assert stored.completed_at is not None


def test_all_success_returns_inference_complete_and_leaves_running(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=3)
    for order in range(3):
        target = item(session, experiment.id, order=order)
        mark_item_completed_with_run(
            session, item=target, run_id=f"run_done_{order}", attempt_id=f"att_done_{order}"
        )

    outcome = step_once(client, experiment.id, "T")
    assert outcome == CoordinatorOutcome.INFERENCE_COMPLETE
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.dataset_evaluation_id is None
        assert fresh.query(DatasetEvaluationModel).count() == 0


def test_empty_experiment_fails_closed(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=2)
    for target in session.query(DatasetExperimentItemModel).filter_by(experiment_id=experiment.id).all():
        session.delete(target)
    session.commit()

    outcome = step_once(client, experiment.id, "T")
    assert outcome == CoordinatorOutcome.INVARIANT_FAILED
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"
