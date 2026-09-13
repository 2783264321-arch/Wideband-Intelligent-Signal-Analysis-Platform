from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from dataset_experiment_fixtures import (
    G3LocalPipeline,
    FakeProvider,
    FakeRegistry,
    create_experiment,
    create_remote_experiment,
    item,
    local_services,
    remote_services,
    running_experiment_with_token,
    seed_dataset,
    seed_remote_pending_run,
)

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments import recovery as rec
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.registry import PipelineRegistry


def _cutoff():
    return datetime.now(timezone.utc)


def _local_deps(provider):
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider})
    return registry, executor_registry


def _recover(client, *, provider, cutoff=None, session=None):
    registry, executor_registry = _local_deps(provider)
    session = session or client.app.state.database.session_factory()
    report = rec.recover_dataset_experiments(
        session, registry=registry, model_release_store=None,
        executor_registry=executor_registry,
        startup_recovery_cutoff=cutoff or _cutoff(),
    )
    return report, session


def _seed_pending_local(session, experiment, *, order=0, marker=None, worker_pid=None, key=None):
    target = item(session, experiment.id, order=order)
    key = order if key is None else key
    session.add(AnalysisRunModel(
        id=f"run_p_{key}", recording_id=target.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="pending",
        parameters_json={}, worker_pid=worker_pid,
    ))
    session.add(DatasetExperimentAttemptModel(
        id=f"att_p_{key}", experiment_item_id=target.id, attempt_number=1,
        analysis_run_id=f"run_p_{key}", launch_requested_at=marker,
    ))
    target.status = "running"
    session.commit()
    return target


def test_claim_generation_rotates_token_clears_pid(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    experiment.worker_pid = 9999
    experiment.heartbeat_at = None
    session.commit()

    token = rec.claim_experiment_generation(session, experiment.id, "T", _cutoff())
    assert token is not None
    assert token != "T"
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.coordinator_token == token
        assert stored.worker_pid is None
        assert stored.heartbeat_at is not None


def test_claim_generation_stale_expected_token_loses(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    cutoff = _cutoff()
    session_a = client.app.state.database.session_factory()
    session_b = client.app.state.database.session_factory()
    first = rec.claim_experiment_generation(session_a, experiment.id, "T", cutoff)
    second = rec.claim_experiment_generation(session_b, experiment.id, "T", cutoff)
    assert first is not None
    assert second is None


def test_claim_generation_ineligible_when_heartbeat_after_cutoff(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    experiment.heartbeat_at = datetime.now(timezone.utc)
    session.commit()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert rec.claim_experiment_generation(session, experiment.id, "T", cutoff) is None


def test_claim_generation_eligible_when_heartbeat_null_or_before_cutoff(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T1", count=1)
    experiment.heartbeat_at = None
    session.commit()
    cutoff = _cutoff()
    assert rec.claim_experiment_generation(session, experiment.id, "T1", cutoff) is not None

    second = create_experiment(ds)
    second.status = "running"
    second.coordinator_token = "T2"
    second.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    session.commit()
    assert rec.claim_experiment_generation(session, second.id, "T2", cutoff) is not None


def test_claim_generation_commit_failure_rolls_back(client, monkeypatch):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        rec.claim_experiment_generation(session, experiment.id, "T", _cutoff())
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).coordinator_token == "T"


def test_fence_loss_diagnostic_rolls_back_and_session_reusable(client, monkeypatch):
    session, ds, analysis, provider, first = running_experiment_with_token(client, "T1", count=1)
    _seed_pending_local(session, first, marker=datetime.now(timezone.utc))
    second = create_experiment(ds)
    second.status = "running"
    second.coordinator_token = "T2"
    session.commit()
    _seed_pending_local(session, second, marker=datetime.now(timezone.utc), key=1)

    def fake(session_, **kwargs):
        session_.execute(select(DatasetExperimentModel.id).limit(1))
        if kwargs["experiment_id"] == first.id:
            raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST", "boom", 409)
        return "already_interrupted"

    monkeypatch.setattr(rec, "fail_closed_pending_local_run", fake)

    report, _ = _recover(client, provider=provider, session=session)
    assert report.skipped == 1
    assert report.claimed == 2
    assert session.in_transaction() is False
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_p_0").status == "pending"


def test_invariant_failure_rolls_back_before_fail_experiment(client, monkeypatch):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None, worker_pid=5)

    observed = {}
    real_fail = DatasetExperimentService._fail_experiment

    def wrapper(self, experiment_id, coordinator_token, error_type, error_message):
        observed["in_transaction"] = self.session.in_transaction()
        return real_fail(self, experiment_id, coordinator_token, error_type, error_message)

    monkeypatch.setattr(DatasetExperimentService, "_fail_experiment", wrapper)

    report, _ = _recover(client, provider=provider, session=session)
    assert observed["in_transaction"] is False
    assert report.invariants_failed == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_safe_first_launch_reuses_same_attempt_and_run(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None)

    report, _ = _recover(client, provider=provider)
    assert report.repaired_first_launch == 1
    assert len(provider.launches) == 1
    with client.app.state.database.session_factory() as fresh:
        attempt = fresh.get(DatasetExperimentAttemptModel, "att_p_0")
        assert attempt.launch_requested_at is not None
        assert fresh.get(AnalysisRunModel, "run_p_0").worker_pid == 4242
        assert fresh.query(DatasetExperimentAttemptModel).count() == 1
        assert fresh.query(AnalysisRunModel).count() == 1


def test_safe_first_launch_sets_marker_before_provider_launch(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None)

    class ProbeProvider(FakeProvider):
        def __init__(self):
            super().__init__("local_cpu")

        def launch(self, run_id, *, coordinator_token):
            with client.app.state.database.session_factory() as fresh:
                attempt = fresh.query(DatasetExperimentAttemptModel).filter_by(
                    analysis_run_id=run_id).one()
                assert attempt.launch_requested_at is not None
            return super().launch(run_id, coordinator_token=coordinator_token)

    probe = ProbeProvider()
    report, _ = _recover(client, provider=probe)
    assert report.repaired_first_launch == 1
    assert len(probe.launches) == 1


def test_safe_first_launch_creates_no_new_attempt_or_run(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None)
    _recover(client, provider=provider)
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(DatasetExperimentAttemptModel).count() == 1
        assert fresh.query(AnalysisRunModel).count() == 1


def test_safe_first_launch_concurrent_actor_cannot_double_launch(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    target = _seed_pending_local(session, experiment, marker=None)
    registry, executor_registry = _local_deps(provider)

    session_a = client.app.state.database.session_factory()
    ds_a = DatasetExperimentService(session_a, registry, None, executor_registry)
    analysis_a = AnalysisService(session_a, registry, None, executor_registry=executor_registry)
    ds_a.launch_item_attempt(
        experiment_id=experiment.id, item_id=target.id, attempt_id="att_p_0",
        analysis_service=analysis_a, coordinator_token="T",
    )

    session_b = client.app.state.database.session_factory()
    ds_b = DatasetExperimentService(session_b, registry, None, executor_registry)
    analysis_b = AnalysisService(session_b, registry, None, executor_registry=executor_registry)
    with pytest.raises(PlatformError):
        ds_b.launch_item_attempt(
            experiment_id=experiment.id, item_id=target.id, attempt_id="att_p_0",
            analysis_service=analysis_b, coordinator_token="T",
        )
    assert len(provider.launches) == 1


def test_ambiguous_pending_local_run_fails_closed_zero_launch(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=datetime.now(timezone.utc))

    report, _ = _recover(client, provider=provider)
    assert report.ambiguous_failed == 1
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, "run_p_0")
        assert run.status == "interrupted"
        assert run.error_type == "ANALYSIS_LAUNCH_AMBIGUOUS"
        assert fresh.query(DatasetExperimentAttemptModel).count() == 1
        assert fresh.query(AnalysisRunModel).count() == 1


def test_ambiguous_fail_close_stale_generation_fence_lost(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T1", count=1)
    target = _seed_pending_local(session, experiment, marker=datetime.now(timezone.utc))
    new_token = rec.claim_experiment_generation(session, experiment.id, "T1", _cutoff())
    assert new_token is not None

    with pytest.raises(PlatformError) as exc:
        rec.fail_closed_pending_local_run(
            session, experiment_id=experiment.id, coordinator_token="T1",
            item_id=target.id, attempt_id="att_p_0", run_id="run_p_0",
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_p_0").status == "pending"
        assert fresh.get(DatasetExperimentItemModel, target.id).status == "running"


def test_ambiguous_fail_close_idempotent_under_current_generation(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T1", count=1)
    target = _seed_pending_local(session, experiment, marker=datetime.now(timezone.utc))

    first = rec.fail_closed_pending_local_run(
        session, experiment_id=experiment.id, coordinator_token="T1",
        item_id=target.id, attempt_id="att_p_0", run_id="run_p_0",
    )
    second = rec.fail_closed_pending_local_run(
        session, experiment_id=experiment.id, coordinator_token="T1",
        item_id=target.id, attempt_id="att_p_0", run_id="run_p_0",
    )
    assert first == "interrupted"
    assert second == "already_interrupted"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_p_0").status == "interrupted"


def test_ambiguous_run_projects_item_failed(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=datetime.now(timezone.utc))
    _recover(client, provider=provider)

    registry, executor_registry = _local_deps(provider)
    with client.app.state.database.session_factory() as fresh:
        ds2 = DatasetExperimentService(fresh, registry, None, executor_registry)
        ds2.reconcile_items(experiment.id)
        target = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id).one()
        assert target.status == "failed"
        assert target.last_error_type == "ANALYSIS_LAUNCH_AMBIGUOUS"


def test_existing_local_running_run_not_relaunched(client):
    from app.remote_execution.recovery import mark_stale_local_cpu_runs_interrupted

    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None)
    run = session.get(AnalysisRunModel, "run_p_0")
    run.status = "running"
    session.commit()
    with client.app.state.database.session_factory() as marker:
        mark_stale_local_cpu_runs_interrupted(marker)

    report, _ = _recover(client, provider=provider)
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_p_0").status == "interrupted"
        target = fresh.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id).one()
        assert target.status == "failed"


def test_remote_pending_run_delegated_no_local_marker_logic(client):
    seed_dataset(client, count=1)
    session, ds, provider, registry, store, executor_registry = remote_services(client)
    experiment = create_remote_experiment(ds, token="T")
    seed_remote_pending_run(session, experiment, marker=None)

    rec.recover_dataset_experiments(
        client.app.state.database.session_factory(),
        registry=registry, model_release_store=store,
        executor_registry=executor_registry, startup_recovery_cutoff=_cutoff(),
    )
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "running"
        assert fresh.get(AnalysisRunModel, "run_remote").status == "pending"


def test_pending_local_run_with_worker_but_null_marker_invariant(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None, worker_pid=5)

    report, _ = _recover(client, provider=provider)
    assert report.invariants_failed == 1
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_missing_run_invariant_fails_experiment(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    target = item(session, experiment.id, order=0)
    session.add(DatasetExperimentAttemptModel(
        id="att_missing", experiment_item_id=target.id, attempt_number=1,
        analysis_run_id="run_missing",
    ))
    target.status = "running"
    session.commit()

    report, _ = _recover(client, provider=provider)
    assert report.invariants_failed == 1
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"


def test_duplicate_active_attempt_invariant_fails_experiment_zero_launch(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    target = item(session, experiment.id, order=0)
    session.add(AnalysisRunModel(
        id="run_d1", recording_id=target.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="pending", parameters_json={},
    ))
    session.add(DatasetExperimentAttemptModel(
        id="att_d1", experiment_item_id=target.id, attempt_number=1, analysis_run_id="run_d1",
    ))
    session.add(AnalysisRunModel(
        id="run_d2", recording_id=target.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="pending", parameters_json={},
    ))
    session.add(DatasetExperimentAttemptModel(
        id="att_d2", experiment_item_id=target.id, attempt_number=2, analysis_run_id="run_d2",
    ))
    target.status = "running"
    session.commit()

    report, _ = _recover(client, provider=provider)
    assert report.invariants_failed == 1
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "failed"
        assert fresh.get(AnalysisRunModel, "run_d1").status == "pending"
        assert fresh.get(AnalysisRunModel, "run_d2").status == "pending"
        assert fresh.query(DatasetExperimentAttemptModel).count() == 2


def test_non_running_experiments_untouched(client):
    seed_dataset(client, count=1)
    session, ds, analysis, provider = local_services(client)
    statuses = ["pending", "completed", "failed", "evaluating"]
    ids = []
    for status in statuses:
        experiment = create_experiment(ds)
        experiment.status = status
        experiment.coordinator_token = f"tok_{status}"
        ids.append(experiment.id)
    session.commit()

    report, _ = _recover(client, provider=provider)
    assert report.claimed == 0
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        for experiment_id, status in zip(ids, statuses):
            assert fresh.get(DatasetExperimentModel, experiment_id).status == status
