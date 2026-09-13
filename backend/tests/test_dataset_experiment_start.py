import pytest

from dataset_experiment_fixtures import (
    G3LocalPipeline,
    RecordingJobManager,
    create_experiment,
    item,
    local_services,
    rotate_token,
    running_experiment_with_token,
    seed_dataset,
)

from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments.model import DatasetExperimentItemModel, DatasetExperimentModel
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.registry import PipelineRegistry

from executor_fixtures import FakeProvider, FakeRegistry


def _fresh_experiment_service(client):
    return DatasetExperimentService(
        client.app.state.database.session_factory(),
        PipelineRegistry([G3LocalPipeline()]),
        None,
        FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
    )


def test_start_experiment_pending_to_running_token_before_spawn(client):
    seed_dataset(client, count=2)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)

    def probe(experiment_id, token):
        with client.app.state.database.session_factory() as fresh:
            stored = fresh.get(DatasetExperimentModel, experiment_id)
            assert stored.status == "running"
            assert stored.coordinator_token == token
            assert stored.worker_pid is None

    job_manager = RecordingJobManager(token_probe=probe)
    started = ds.start_experiment(experiment.id, job_manager)
    assert started.status == "running"
    assert started.coordinator_token is not None
    assert started.worker_pid == 4242
    assert len(job_manager.calls) == 1


def test_start_experiment_real_two_session_stale_pending_cas(client):
    seed_dataset(client, count=2)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    assert session.get(DatasetExperimentModel, experiment.id).status == "pending"

    winner_jm = RecordingJobManager(pid=1111)
    _fresh_experiment_service(client).start_experiment(experiment.id, winner_jm)

    loser_jm = RecordingJobManager(pid=2222)
    with pytest.raises(PlatformError) as exc:
        ds.start_experiment(experiment.id, loser_jm)
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
    assert loser_jm.calls == []
    assert len(winner_jm.calls) == 1
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "running"
        assert stored.coordinator_token is not None
        assert stored.worker_pid == 1111


def test_start_experiment_spawn_failure_marks_failed(client):
    seed_dataset(client, count=2)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    with pytest.raises(PlatformError) as exc:
        ds.start_experiment(experiment.id, RecordingJobManager(fail=True))
    assert exc.value.code == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"
        assert stored.worker_pid is None


def test_start_experiment_commit_failure_zero_spawn(client, monkeypatch):
    seed_dataset(client, count=2)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    job_manager = RecordingJobManager()

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        ds.start_experiment(experiment.id, job_manager)
    assert job_manager.calls == []


def test_start_experiment_does_not_overwrite_terminal_worker_pid(client):
    seed_dataset(client, count=2)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)

    def terminate(experiment_id, token):
        with client.app.state.database.session_factory() as other:
            stored = other.get(DatasetExperimentModel, experiment_id)
            stored.status = "completed_with_failures"
            other.commit()

    ds.start_experiment(experiment.id, RecordingJobManager(on_start=terminate))
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "completed_with_failures"
        assert stored.worker_pid is None


def test_start_experiment_rejects_missing_and_non_pending(client):
    seed_dataset(client, count=2)
    session, ds, analysis, provider = local_services(client)
    with pytest.raises(PlatformError) as missing:
        ds.start_experiment("missing", RecordingJobManager())
    assert missing.value.code == "DATASET_EXPERIMENT_NOT_FOUND"

    experiment = create_experiment(ds)
    experiment.status = "completed"
    session.commit()
    with pytest.raises(PlatformError) as non_pending:
        ds.start_experiment(experiment.id, RecordingJobManager())
    assert non_pending.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"


def test_mark_item_failed_wrong_experiment_fails_closed(client):
    session, ds, analysis, provider, experiment_a = running_experiment_with_token(client, "coord_A", count=2)
    experiment_b = create_experiment(ds)
    experiment_b.status = "running"
    experiment_b.coordinator_token = "coord_A"
    session.commit()
    item_b = item(session, experiment_b.id)

    with pytest.raises(PlatformError) as exc:
        ds._mark_item_failed(item_b.id, "X", "y", experiment_id=experiment_a.id,
                             coordinator_token="coord_A")
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment_b.id) is not None
        stored_b = fresh.get(DatasetExperimentModel, experiment_b.id)
        assert stored_b.status == "running"


def test_mark_item_failed_already_failed_is_idempotent(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "coord_A", count=2)
    target = item(session, experiment.id)
    target.status = "failed"
    session.commit()

    result = ds._mark_item_failed(target.id, "X", "y", experiment_id=experiment.id,
                                  coordinator_token="coord_A")
    assert result is False
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, target.id).status == "failed"


def test_mark_item_failed_queued_projects_failed_once(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "coord_A", count=2)
    target = item(session, experiment.id)

    assert ds._mark_item_failed(target.id, "INPUT_INCOMPATIBLE", "bad",
                                experiment_id=experiment.id, coordinator_token="coord_A") is True
    assert ds._mark_item_failed(target.id, "INPUT_INCOMPATIBLE", "bad",
                                experiment_id=experiment.id, coordinator_token="coord_A") is False
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentItemModel, target.id)
        assert stored.status == "failed"
        assert stored.last_error_type == "INPUT_INCOMPATIBLE"
        assert stored.last_error_message == "bad"


def test_mark_item_failed_running_projects_failed_once(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "coord_A", count=2)
    target = item(session, experiment.id)
    target.status = "running"
    session.commit()

    assert ds._mark_item_failed(target.id, "ANALYSIS_FAILED", "boom",
                                experiment_id=experiment.id, coordinator_token="coord_A") is True
    assert ds._mark_item_failed(target.id, "ANALYSIS_FAILED", "boom",
                                experiment_id=experiment.id, coordinator_token="coord_A") is False
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentItemModel, target.id)
        assert stored.status == "failed"
        assert stored.last_error_type == "ANALYSIS_FAILED"


def test_mark_item_failed_stale_generation_fence_lost(client):
    session, ds, analysis, provider, experiment = running_experiment_with_token(client, "coord_A", count=2)
    target = item(session, experiment.id)
    rotate_token(client, experiment.id, "coord_B")
    with pytest.raises(PlatformError) as exc:
        ds._mark_item_failed(target.id, "X", "y", experiment_id=experiment.id,
                             coordinator_token="coord_A")
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, target.id).status == "queued"
