import pytest

from benchmark_fixture import add_detection, add_ground_truth, add_recording, add_run

from dataset_experiment_fixtures import create_experiment, local_services, seed_dataset

from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments.model import DatasetExperimentModel


def _build_tiny_dataset(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_recording(session, recording_id="rec_b", name="b")
        add_ground_truth(session, gt_id="gt_a", recording_id="rec_a", class_id=9,
                         class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_ground_truth(session, gt_id="gt_b", recording_id="rec_b", class_id=6,
                         class_name="BLE LE1M", t0=0.03, t1=0.04,
                         f0=2_440_800_000.0, f1=2_440_900_000.0)
        add_run(session, run_id="run_a", recording_id="rec_a", pipeline_id="pipeline_x",
                pipeline_version="1.0", executor="imported")
        add_run(session, run_id="run_b", recording_id="rec_b", pipeline_id="pipeline_x",
                pipeline_version="1.0", executor="imported")
        add_detection(session, detection_id="det_a", run_id="run_a", class_id=9,
                      class_name="LoRa 250kHz", confidence=0.9, t0=0.01, t1=0.02,
                      f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_detection(session, detection_id="det_b", run_id="run_b", class_id=6,
                      class_name="BLE LE1M", confidence=0.8, t0=0.03, t1=0.04,
                      f0=2_440_800_000.0, f1=2_440_900_000.0)
        session.commit()


def _create_evaluation(client, *, name="tiny"):
    database = client.app.state.database
    with database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        evaluation = svc.create_evaluation(
            name=name, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=[{"recording_id": "rec_a", "analysis_run_id": "run_a"},
                   {"recording_id": "rec_b", "analysis_run_id": "run_b"}],
        )
        return evaluation.id


def _status(client, evaluation_id):
    with client.app.state.database.session_factory() as session:
        return session.get(DatasetEvaluationModel, evaluation_id).status


def _set_status(client, evaluation_id, status):
    with client.app.state.database.session_factory() as session:
        evaluation = session.get(DatasetEvaluationModel, evaluation_id)
        evaluation.status = status
        session.commit()


def _link_experiment(client, evaluation_id):
    seed_dataset(client, count=1)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds)
    experiment.status = "evaluating"
    experiment.dataset_evaluation_id = evaluation_id
    session.commit()
    return experiment, session


class RecordingBenchmarkJobManager:
    def __init__(self, *, on_start=None, fail=False, pid=4242, session=None):
        self.calls = []
        self.on_start = on_start
        self.fail = fail
        self.pid = pid
        self.session = session

    def start(self, evaluation_id):
        self.calls.append(evaluation_id)
        if self.on_start is not None:
            self.on_start(evaluation_id)
        if self.fail:
            raise OSError("cannot spawn benchmark worker")
        return self.pid


def test_prepare_evaluation_stages_without_commit(client):
    _build_tiny_dataset(client)
    database = client.app.state.database
    with database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        evaluation = svc.prepare_evaluation(
            name="staged", dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=[{"recording_id": "rec_a", "analysis_run_id": "run_a"},
                   {"recording_id": "rec_b", "analysis_run_id": "run_b"}],
        )
        evaluation_id = evaluation.id
        assert evaluation.status == "pending"
    with database.session_factory() as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation_id) is None
        assert fresh.query(DatasetEvaluationItemModel).count() == 0


def test_create_evaluation_composes_prepare_and_commit(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    with client.app.state.database.session_factory() as session:
        evaluation = session.get(DatasetEvaluationModel, evaluation_id)
        assert evaluation.status == "pending"
        assert session.query(DatasetEvaluationItemModel).filter_by(
            evaluation_id=evaluation_id).count() == 2


def test_start_evaluation_spawns_outside_transaction(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    session = client.app.state.database.session_factory()
    svc = DatasetBenchmarkService(session)

    observed = {}

    def probe(eid):
        with client.app.state.database.session_factory() as fresh:
            stored = fresh.get(DatasetEvaluationModel, eid)
            observed["status"] = stored.status
            observed["started_at"] = stored.started_at
        observed["in_transaction"] = session.in_transaction()

    job_manager = RecordingBenchmarkJobManager(on_start=probe)
    svc.start_evaluation(evaluation_id, job_manager)
    assert observed["in_transaction"] is False
    assert observed["status"] == "running"
    assert observed["started_at"] is not None


def test_start_evaluation_concurrent_two_sessions_one_winner(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    session_a = client.app.state.database.session_factory()
    session_b = client.app.state.database.session_factory()
    job_manager = RecordingBenchmarkJobManager()

    DatasetBenchmarkService(session_a).start_evaluation(evaluation_id, job_manager)
    with pytest.raises(PlatformError) as exc:
        DatasetBenchmarkService(session_b).start_evaluation(evaluation_id, job_manager)
    assert exc.value.code == "INVALID_BENCHMARK_TRANSITION"
    assert job_manager.calls == [evaluation_id]


def test_start_evaluation_spawn_failure_running_to_failed(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    session = client.app.state.database.session_factory()
    svc = DatasetBenchmarkService(session)
    with pytest.raises(PlatformError) as exc:
        svc.start_evaluation(evaluation_id, RecordingBenchmarkJobManager(fail=True))
    assert exc.value.code == "BENCHMARK_FAILED"
    with client.app.state.database.session_factory() as fresh:
        evaluation = fresh.get(DatasetEvaluationModel, evaluation_id)
        assert evaluation.status == "failed"
        assert evaluation.error_type == "BENCHMARK_FAILED"


def test_start_evaluation_fast_worker_terminal_not_overwritten(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    session = client.app.state.database.session_factory()
    svc = DatasetBenchmarkService(session)

    def complete(eid):
        with client.app.state.database.session_factory() as other:
            evaluation = other.get(DatasetEvaluationModel, eid)
            evaluation.status = "completed"
            other.commit()

    svc.start_evaluation(evaluation_id, RecordingBenchmarkJobManager(on_start=complete, pid=777))
    with client.app.state.database.session_factory() as fresh:
        evaluation = fresh.get(DatasetEvaluationModel, evaluation_id)
        assert evaluation.status == "completed"
        assert evaluation.worker_pid == 777


def test_prepare_retry_evaluation_stages_without_commit(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    _set_status(client, evaluation_id, "failed")
    session = client.app.state.database.session_factory()
    svc = DatasetBenchmarkService(session)
    svc.prepare_retry_evaluation(evaluation_id)
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation_id).status == "failed"
    session.rollback()


def test_retry_evaluation_public_behavior_unchanged(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    _set_status(client, evaluation_id, "failed")
    with client.app.state.database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        returned = svc.retry_evaluation(evaluation_id)
        assert returned.status == "pending"
    assert _status(client, evaluation_id) == "pending"


def test_retry_evaluation_rejects_dataset_experiment_linked_failed(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    _set_status(client, evaluation_id, "failed")
    experiment, session = _link_experiment(client, evaluation_id)
    with client.app.state.database.session_factory() as bench:
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(bench).retry_evaluation(evaluation_id)
        assert exc.value.code == "BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT"
    assert _status(client, evaluation_id) == "failed"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentModel, experiment.id).dataset_evaluation_id == evaluation_id


def test_retry_evaluation_rejects_dataset_experiment_linked_interrupted(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    _set_status(client, evaluation_id, "interrupted")
    _link_experiment(client, evaluation_id)
    with client.app.state.database.session_factory() as bench:
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(bench).retry_evaluation(evaluation_id)
        assert exc.value.code == "BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT"
    assert _status(client, evaluation_id) == "interrupted"


def test_prepare_retry_evaluation_allows_linked_evaluation(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    _set_status(client, evaluation_id, "failed")
    _link_experiment(client, evaluation_id)
    session = client.app.state.database.session_factory()
    svc = DatasetBenchmarkService(session)
    returned = svc.prepare_retry_evaluation(evaluation_id)
    assert returned.status == "pending"
    session.rollback()


def test_restore_retry_evaluation_roundtrip(client):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client)
    _set_status(client, evaluation_id, "failed")
    session = client.app.state.database.session_factory()
    svc = DatasetBenchmarkService(session)
    snapshot = svc.snapshot_retry_evaluation(evaluation_id)
    svc.prepare_retry_evaluation(evaluation_id)
    svc.restore_retry_evaluation(evaluation_id, snapshot)
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetEvaluationModel, evaluation_id).status == "failed"
    session.rollback()
