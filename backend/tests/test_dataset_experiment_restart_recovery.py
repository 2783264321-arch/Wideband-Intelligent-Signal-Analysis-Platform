from datetime import datetime, timedelta, timezone

import pytest

from dataset_experiment_fixtures import (
    G3LocalPipeline,
    FakeProvider,
    FakeRegistry,
    RecordingJobManager,
    create_experiment,
    create_remote_experiment,
    item,
    local_services,
    mark_item_completed_with_run,
    remote_services,
    running_experiment_with_token,
    seed_dataset,
    seed_remote_pending_run,
    step_once,
)

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.dataset_experiments import recovery as rec
from app.dataset_experiments.coordinator import CoordinatorOutcome
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.pipelines.registry import PipelineRegistry


def _cutoff():
    return datetime.now(timezone.utc)


def _later_cutoff():
    return datetime.now(timezone.utc) + timedelta(seconds=5)


def _recover(client, *, provider=None, registry=None, model_release_store=None,
             executor_registry=None, job_manager, cutoff=None, session=None):
    if registry is None:
        registry = PipelineRegistry([G3LocalPipeline()])
        executor_registry = FakeRegistry({"local_cpu": provider or FakeProvider("local_cpu")})
        model_release_store = None
    session = session or client.app.state.database.session_factory()
    report = rec.recover_dataset_experiments(
        session, job_manager=job_manager, registry=registry,
        model_release_store=model_release_store, executor_registry=executor_registry,
        startup_recovery_cutoff=cutoff or _cutoff(),
    )
    return report, session


def _seed_pending_local(session, experiment, *, marker=None, worker_pid=None):
    target = item(session, experiment.id, order=0)
    session.add(AnalysisRunModel(
        id="run_p_0", recording_id=target.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="pending",
        parameters_json={}, worker_pid=worker_pid,
    ))
    session.add(DatasetExperimentAttemptModel(
        id="att_p_0", experiment_item_id=target.id, attempt_number=1,
        analysis_run_id="run_p_0", launch_requested_at=marker,
    ))
    target.status = "running"
    session.commit()
    return target


def test_restart_spawns_coordinator_with_fresh_token(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    job_manager = RecordingJobManager()
    report, _ = _recover(client, provider=provider, job_manager=job_manager)
    assert report.coordinators_started == 1
    spawned_id, spawned_token = job_manager.calls[0]
    assert spawned_id == experiment.id
    assert spawned_token != "T"
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.coordinator_token == spawned_token
        assert stored.worker_pid == 4242


def test_restart_token_persisted_before_spawn(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)

    def probe(experiment_id, token):
        with client.app.state.database.session_factory() as fresh:
            stored = fresh.get(DatasetExperimentModel, experiment_id)
            assert stored.status == "running"
            assert stored.coordinator_token == token
            assert stored.worker_pid is None

    _recover(client, provider=provider, job_manager=RecordingJobManager(token_probe=probe))


def test_restart_old_generation_fenced(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _recover(client, provider=provider, job_manager=RecordingJobManager())
    outcome = step_once(client, experiment.id, "T")
    assert outcome == CoordinatorOutcome.FENCE_LOST


def test_restart_stale_pid_not_authority(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    experiment.worker_pid = 7777
    session.commit()
    report, _ = _recover(client, provider=provider, job_manager=RecordingJobManager(pid=4242))
    assert report.coordinators_started == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.worker_pid == 4242
        assert stored.coordinator_token != "T"


def test_restart_spawn_failure_leaves_running_and_recoverable(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    first = _recover(client, provider=provider, job_manager=RecordingJobManager(fail=True))
    report_a, _ = first
    assert report_a.spawn_failures == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.worker_pid is None
        assert stored.coordinator_token != "T"
        first_token = stored.coordinator_token

    report_b, _ = _recover(
        client, provider=provider, job_manager=RecordingJobManager(pid=4242),
        cutoff=_later_cutoff(),
    )
    assert report_b.coordinators_started == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.worker_pid == 4242
        assert stored.coordinator_token != first_token


def test_same_startup_second_recovery_cannot_steal_fresh_generation(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    cutoff = _cutoff()
    manager_a = RecordingJobManager(pid=1111)
    report_a, _ = _recover(client, provider=provider, job_manager=manager_a, cutoff=cutoff)
    assert report_a.coordinators_started == 1

    manager_b = RecordingJobManager(pid=2222)
    report_b, _ = _recover(client, provider=provider, job_manager=manager_b, cutoff=cutoff)
    assert report_b.skipped == 1
    assert report_b.coordinators_started == 0
    assert manager_b.calls == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).worker_pid == 1111


def test_next_restart_later_cutoff_recovers_crash_after_claim(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    report_a, _ = _recover(
        client, provider=provider, job_manager=RecordingJobManager(fail=True),
        cutoff=_cutoff(),
    )
    assert report_a.spawn_failures == 1

    manager_b = RecordingJobManager(pid=4242)
    report_b, _ = _recover(
        client, provider=provider, job_manager=manager_b, cutoff=_later_cutoff(),
    )
    assert report_b.coordinators_started == 1
    assert len(manager_b.calls) == 1


def test_b_committed_c_not_launched_same_epoch_recovery_cannot_interrupt(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    _seed_pending_local(session, experiment, marker=None)

    cutoff = _cutoff()
    claimed = rec.claim_experiment_generation(session, experiment.id, "T", cutoff)
    assert claimed is not None
    attempt = session.get(DatasetExperimentAttemptModel, "att_p_0")
    attempt.launch_requested_at = datetime.now(timezone.utc)  # Transaction B commit
    session.commit()

    manager_b = RecordingJobManager(pid=2222)
    report_b, _ = _recover(client, provider=provider, job_manager=manager_b, cutoff=cutoff)
    assert report_b.skipped == 1
    assert report_b.coordinators_started == 0
    assert manager_b.calls == []
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_p_0").status == "pending"
        assert fresh.get(DatasetExperimentModel, experiment.id).coordinator_token == claimed


def test_restart_claim_two_sessions_same_expected_token_one_winner(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    cutoff = _cutoff()
    session_a = client.app.state.database.session_factory()
    session_b = client.app.state.database.session_factory()
    first = rec.claim_experiment_generation(session_a, experiment.id, "T", cutoff)
    second = rec.claim_experiment_generation(session_b, experiment.id, "T", cutoff)
    assert first is not None
    assert second is None


def test_all_success_seam_restart_no_new_run_and_still_running(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=2)
    for order in range(2):
        target = item(session, experiment.id, order=order)
        mark_item_completed_with_run(
            session, item=target, run_id=f"run_done_{order}", attempt_id=f"att_done_{order}"
        )

    job_manager = RecordingJobManager()
    report, _ = _recover(client, provider=provider, job_manager=job_manager)
    assert report.coordinators_started == 1
    spawned_token = job_manager.calls[0][1]

    outcome = step_once(client, experiment.id, spawned_token)
    assert outcome == CoordinatorOutcome.INFERENCE_COMPLETE
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.dataset_evaluation_id is None
        assert fresh.query(DatasetEvaluationModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 2
        assert fresh.query(AnalysisRunModel).count() == 2


def test_evaluating_experiment_not_selected_by_g4(client):
    seed_dataset(client, count=1)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    experiment.status = "evaluating"
    experiment.coordinator_token = "T"
    session.commit()

    report, _ = _recover(client, provider=provider, job_manager=RecordingJobManager())
    assert report.claimed == 0
    assert report.coordinators_started == 0
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "evaluating"


def test_recover_pid_commit_failure_rolls_back(client, monkeypatch):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=1)
    real_commit = session.commit
    counter = {"n": 0}

    def flaky_commit():
        counter["n"] += 1
        if counter["n"] == 3:
            raise RuntimeError("commit failed")
        real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)
    with pytest.raises(RuntimeError):
        _recover(
            client, provider=provider, job_manager=RecordingJobManager(pid=4242),
            session=session,
        )
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.worker_pid is None
        assert stored.coordinator_token != "T"


def test_all_success_read_path_spawns_outside_transaction(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "T", count=2)
    for order in range(2):
        target = item(session, experiment.id, order=order)
        mark_item_completed_with_run(
            session, item=target, run_id=f"run_done_{order}", attempt_id=f"att_done_{order}"
        )

    observed = {}

    def on_start(experiment_id, token):
        observed["in_transaction"] = session.in_transaction()

    report, _ = _recover(
        client, provider=provider,
        job_manager=RecordingJobManager(on_start=on_start), session=session,
    )
    assert observed["in_transaction"] is False
    assert report.coordinators_started == 1
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).status == "running"
        assert fresh.query(DatasetExperimentAttemptModel).count() == 2
        assert fresh.query(AnalysisRunModel).count() == 2


def test_read_only_repair_path_closes_autobegin_transaction(client):
    seed_dataset(client, count=1)
    rsession, rds, rprovider, registry, store, executor_registry = remote_services(client)
    experiment = create_remote_experiment(rds, token="T")
    seed_remote_pending_run(rsession, experiment, marker=None)

    session = client.app.state.database.session_factory()
    observed = {}

    def on_start(experiment_id, token):
        observed["in_transaction"] = session.in_transaction()

    report, _ = _recover(
        client, registry=registry, model_release_store=store,
        executor_registry=executor_registry,
        job_manager=RecordingJobManager(on_start=on_start), session=session,
    )
    assert observed["in_transaction"] is False
    assert report.coordinators_started == 1
    assert rprovider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, "run_remote").status == "pending"
