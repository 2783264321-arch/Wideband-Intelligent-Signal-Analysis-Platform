import time

import pytest

from benchmark_fixture import add_detection, add_ground_truth, add_recording, add_run

from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL
from app.benchmarks.worker import execute_benchmark
from app.db.base import Base, load_domain_models
from app.db.session import Database


def _build_tiny_dataset(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_recording(session, recording_id="rec_b", name="b")
        add_ground_truth(session, gt_id="gt_a", recording_id="rec_a", class_id=9, class_name="LoRa 250kHz",
                         t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_ground_truth(session, gt_id="gt_b", recording_id="rec_b", class_id=6, class_name="BLE LE1M",
                         t0=0.03, t1=0.04, f0=2_440_800_000.0, f1=2_440_900_000.0)
        add_run(session, run_id="run_a", recording_id="rec_a", pipeline_id="pipeline_x", pipeline_version="1.0", executor="imported")
        add_run(session, run_id="run_b", recording_id="rec_b", pipeline_id="pipeline_x", pipeline_version="1.0", executor="imported")
        add_detection(session, detection_id="det_a", run_id="run_a", class_id=9, class_name="LoRa 250kHz", confidence=0.9,
                      t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_detection(session, detection_id="det_b", run_id="run_b", class_id=6, class_name="BLE LE1M", confidence=0.8,
                      t0=0.03, t1=0.04, f0=2_440_800_000.0, f1=2_440_900_000.0)
        session.commit()
    return database


def _create_evaluation(client, settings):
    database = client.app.state.database
    with database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        evaluation = svc.create_evaluation(
            name="tiny", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=[
                {"recording_id": "rec_a", "analysis_run_id": "run_a"},
                {"recording_id": "rec_b", "analysis_run_id": "run_b"},
            ],
        )
        return evaluation.id


def _get(client, evaluation_id):
    with client.app.state.database.session_factory() as session:
        return session.get(DatasetEvaluationModel, evaluation_id)


def test_pending_to_running_to_completed(client, settings):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client, settings)
    execute_benchmark(evaluation_id, settings)
    evaluation = _get(client, evaluation_id)
    assert evaluation.status == "completed"
    assert evaluation.aggregate_metrics_json is not None
    assert evaluation.per_class_metrics_json is not None
    assert evaluation.confusion_json is not None
    aggregate = evaluation.aggregate_metrics_json
    assert aggregate["localization"]["ap50"] == 1.0
    assert aggregate["localization"]["ap50_95"] == 1.0
    assert aggregate["localization"]["operating"]["tp"] == 2
    assert aggregate["localization"]["operating"]["fn"] == 0
    assert aggregate["classification_applicable"] is True
    assert aggregate["class_aware"]["map50"] == 1.0
    assert aggregate["classification_on_matched"]["matched_accuracy"] == 1.0


def test_detection_only_worker_stores_na_null_never_zero(client, settings):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_ground_truth(session, gt_id="gt_a", recording_id="rec_a", class_id=9, class_name="LoRa 250kHz",
                         t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_run(session, run_id="run_a", recording_id="rec_a", pipeline_id="stft_energy_detector",
                pipeline_version="1.0", executor="local_cpu")
        add_detection(session, detection_id="det_a", run_id="run_a", class_id=0, class_name="Signal", confidence=0.9,
                      t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()
    with database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        evaluation_id = svc.create_evaluation(
            name="tiny", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=[{"recording_id": "rec_a", "analysis_run_id": "run_a"}],
        ).id
    execute_benchmark(evaluation_id, settings)
    evaluation = _get(client, evaluation_id)
    assert evaluation.status == "completed"
    aggregate = evaluation.aggregate_metrics_json
    assert aggregate["classification_applicable"] is False
    assert aggregate["classification_reason"] == "detection_only_pipeline"
    assert aggregate["classification_on_matched"] is None
    assert aggregate["class_aware"] is None
    assert evaluation.per_class_metrics_json == []
    assert evaluation.confusion_json is None
    assert aggregate["localization"]["ap50"] == 1.0


def test_worker_exception_sets_failed_and_no_formal_json(client, settings, monkeypatch):
    import app.benchmarks.worker as worker_module

    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client, settings)

    def _boom(*args, **kwargs):
        raise RuntimeError("injected compute failure")

    monkeypatch.setattr(worker_module, "_compute_results", _boom)
    with pytest.raises(RuntimeError):
        execute_benchmark(evaluation_id, settings)
    evaluation = _get(client, evaluation_id)
    assert evaluation.status == "failed"
    assert evaluation.aggregate_metrics_json is None
    assert evaluation.per_class_metrics_json is None
    assert evaluation.confusion_json is None
    assert evaluation.error_type == "RuntimeError"
    assert "injected compute failure" in (evaluation.error_message or "")


def test_stale_running_marked_interrupted(client, settings):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client, settings)
    with client.app.state.database.session_factory() as session:
        evaluation = session.get(DatasetEvaluationModel, evaluation_id)
        evaluation.status = "running"
        evaluation.worker_pid = 999999
        session.commit()
    from app.benchmarks.service import mark_stale_running_evaluations_interrupted
    with client.app.state.database.session_factory() as session:
        mark_stale_running_evaluations_interrupted(session)
    evaluation = _get(client, evaluation_id)
    assert evaluation.status == "interrupted"
    assert evaluation.error_type == "BENCHMARK_INTERRUPTED"


def test_retry_keeps_exact_membership(client, settings):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client, settings)
    with client.app.state.database.session_factory() as session:
        evaluation = session.get(DatasetEvaluationModel, evaluation_id)
        evaluation.status = "failed"
        evaluation.error_type = "RuntimeError"
        evaluation.error_message = "boom"
        session.commit()
    from app.benchmarks.service import DatasetBenchmarkService
    with client.app.state.database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        svc.retry_evaluation(evaluation_id)
    with client.app.state.database.session_factory() as session:
        from sqlalchemy.orm import selectinload
        evaluation = session.get(
            DatasetEvaluationModel, evaluation_id,
            options=[selectinload(DatasetEvaluationModel.items)],
        )
    assert evaluation.status == "running" or evaluation.status == "pending"
    item_run_ids = [item.analysis_run_id for item in sorted(evaluation.items, key=lambda i: i.manifest_order)]
    assert item_run_ids == ["run_a", "run_b"]
    assert evaluation.evaluation_protocol == PHYSICAL_TF_PROTOCOL


def test_completed_evaluation_cannot_rerun(client, settings):
    _build_tiny_dataset(client)
    evaluation_id = _create_evaluation(client, settings)
    execute_benchmark(evaluation_id, settings)
    from app.benchmarks.service import DatasetBenchmarkService
    with client.app.state.database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        with pytest.raises(Exception):
            svc.start_evaluation(evaluation_id)