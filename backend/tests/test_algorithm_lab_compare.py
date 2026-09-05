from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


def _add_recording(session, *, rec_id="rec_cmp", has_gt=True, label_space="spacenet_14"):
    recording = RecordingModel(
        id=rec_id,
        name="cmp",
        data_path="recordings/rec_cmp/raw.iq",
        data_format="complex64_le",
        source="custom",
        sample_rate_hz=50_000_000.0,
        center_frequency_hz=2_455_000_000.0,
        frequency_low_hz=2_430_000_000.0,
        frequency_high_hz=2_480_000_000.0,
        num_samples=7_500_000,
        duration_s=0.15,
        label_space=label_space,
        has_ground_truth=has_gt,
    )
    session.add(recording)
    return recording


def _add_run(session, *, run_id, rec_id, status="completed", pipeline_id="stft_energy_detector", pipeline_version="1.0"):
    run = AnalysisRunModel(
        id=run_id,
        recording_id=rec_id,
        pipeline_id=pipeline_id,
        pipeline_version=pipeline_version,
        executor="local_cpu",
        status=status,
        parameters_json={},
    )
    session.add(run)
    return run


def _add_gt(session, *, gt_id, rec_id, t0, t1, f0, f1, class_id=9, class_name="LoRa 250kHz"):
    session.add(GroundTruthModel(
        id=gt_id, recording_id=rec_id, t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1,
        class_id=class_id, class_name=class_name,
    ))


def _add_detection(session, *, det_id, run_id, t0, t1, f0, f1, confidence=0.9, class_id=0, class_name="Signal"):
    session.add(DetectionResultModel(
        id=det_id, run_id=run_id, t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1,
        class_id=class_id, class_name=class_name, confidence=confidence,
    ))


def _populate_comparison_fixture(client) -> None:
    database = client.app.state.database
    with database.session_factory() as session:
        _add_recording(session)
        _add_run(session, run_id="run_a", rec_id="rec_cmp")
        _add_run(session, run_id="run_b", rec_id="rec_cmp", pipeline_id="zoomspec", pipeline_version="1.0")
        # GT0..GT3 span distinct time windows; extra prediction is a clear FP.
        _add_gt(session, gt_id="gt0", rec_id="rec_cmp", t0=0.00, t1=0.02, f0=2_440_000_000.0, f1=2_441_000_000.0, class_id=9)
        _add_gt(session, gt_id="gt1", rec_id="rec_cmp", t0=0.02, t1=0.04, f0=2_440_000_000.0, f1=2_441_000_000.0, class_id=2)
        _add_gt(session, gt_id="gt2", rec_id="rec_cmp", t0=0.04, t1=0.06, f0=2_440_000_000.0, f1=2_441_000_000.0, class_id=6)
        _add_gt(session, gt_id="gt3", rec_id="rec_cmp", t0=0.06, t1=0.08, f0=2_440_000_000.0, f1=2_441_000_000.0, class_id=13)
        # Run A matches GT0 and GT1; GT2/GT3 missed; one FP prediction.
        _add_detection(session, det_id="det_a0", run_id="run_a", t0=0.00, t1=0.02, f0=2_440_000_000.0, f1=2_441_000_000.0)
        _add_detection(session, det_id="det_a1", run_id="run_a", t0=0.02, t1=0.04, f0=2_440_000_000.0, f1=2_441_000_000.0)
        _add_detection(session, det_id="det_a_fp", run_id="run_a", t0=0.09, t1=0.10, f0=2_460_000_000.0, f1=2_461_000_000.0)
        # Run B matches GT0 and GT2; GT1/GT3 missed; no FP.
        _add_detection(session, det_id="det_b0", run_id="run_b", t0=0.00, t1=0.02, f0=2_440_000_000.0, f1=2_441_000_000.0)
        _add_detection(session, det_id="det_b2", run_id="run_b", t0=0.04, t1=0.06, f0=2_440_000_000.0, f1=2_441_000_000.0)
        session.commit()


def _compare(client, *, rec_id="rec_cmp", run_a="run_a", run_b="run_b", threshold=0.5):
    return client.post(
        "/api/algorithm-lab/compare",
        json={
            "recording_id": rec_id,
            "run_a_id": run_a,
            "run_b_id": run_b,
            "iou_threshold": threshold,
        },
    )


def test_compare_rejects_same_run_twice(client):
    _populate_comparison_fixture(client)
    response = _compare(client, run_a="run_a", run_b="run_a")
    assert response.status_code == 422


def test_compare_rejects_missing_run(client):
    _populate_comparison_fixture(client)
    response = _compare(client, run_b="run_missing")
    assert response.status_code == 404


def test_compare_rejects_non_completed_run(client):
    database = client.app.state.database
    with database.session_factory() as session:
        _add_recording(session)
        _add_run(session, run_id="run_a", rec_id="rec_cmp")
        _add_run(session, run_id="run_b", rec_id="rec_cmp", status="running")
        session.commit()
    response = _compare(client)
    assert response.status_code == 422


def test_compare_rejects_runs_on_different_recordings(client):
    database = client.app.state.database
    with database.session_factory() as session:
        _add_recording(session, rec_id="rec_other")
        _add_recording(session)
        _add_run(session, run_id="run_a", rec_id="rec_cmp")
        _add_run(session, run_id="run_b", rec_id="rec_other")
        _add_gt(session, gt_id="gt0", rec_id="rec_cmp", t0=0.0, t1=0.02, f0=2_440_000_000.0, f1=2_441_000_000.0)
        session.commit()
    response = _compare(client, rec_id="rec_cmp")
    assert response.status_code == 422


def test_compare_rejects_recording_without_ground_truth(client):
    database = client.app.state.database
    with database.session_factory() as session:
        _add_recording(session, has_gt=False)
        _add_run(session, run_id="run_a", rec_id="rec_cmp")
        _add_run(session, run_id="run_b", rec_id="rec_cmp")
        session.commit()
    response = _compare(client)
    assert response.status_code == 422


def test_compare_success_metrics_and_all_four_cases(client):
    _populate_comparison_fixture(client)
    response = _compare(client)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["recording_id"] == "rec_cmp"
    assert payload["iou_threshold"] == 0.5

    run_a = payload["run_a"]
    assert run_a["run_id"] == "run_a"
    assert run_a["pipeline_id"] == "stft_energy_detector"
    assert run_a["metrics"]["tp"] == 2
    assert run_a["metrics"]["fp"] == 1
    assert run_a["metrics"]["fn"] == 2
    assert run_a["metrics"]["precision"] == 2 / 3
    assert run_a["metrics"]["recall"] == 0.5

    run_b = payload["run_b"]
    assert run_b["metrics"]["tp"] == 2
    assert run_b["metrics"]["fp"] == 0
    assert run_b["metrics"]["fn"] == 2
    assert run_b["metrics"]["precision"] == 1.0
    assert run_b["metrics"]["recall"] == 0.5

    cases = {case["ground_truth_id"]: case for case in payload["cases"]}
    assert len(cases) == 4
    assert cases["gt0"]["comparison"] == "both_detected"
    assert cases["gt0"]["run_a"]["matched"] is True
    assert cases["gt0"]["run_a"]["detection_id"] == "det_a0"
    assert cases["gt0"]["run_b"]["matched"] is True
    assert cases["gt1"]["comparison"] == "a_only"
    assert cases["gt1"]["run_a"]["matched"] is True
    assert cases["gt1"]["run_b"]["matched"] is False
    assert cases["gt1"]["run_b"]["detection_id"] is None
    assert cases["gt2"]["comparison"] == "b_only"
    assert cases["gt2"]["run_a"]["matched"] is False
    assert cases["gt2"]["run_b"]["matched"] is True
    assert cases["gt3"]["comparison"] == "both_missed"
    assert cases["gt3"]["run_a"]["matched"] is False
    assert cases["gt3"]["run_b"]["matched"] is False

    # The extra FP prediction appears only in run A metrics, not as a GT case row.
    assert all(len(case["run_a"].get("bbox") or {}) in (0, 4) for case in payload["cases"])
    assert cases["gt0"]["run_a"]["bbox"]["f_high_hz"] == 2_441_000_000.0


def test_compare_rejects_non_default_threshold(client):
    _populate_comparison_fixture(client)
    response = _compare(client, threshold=0.7)
    assert response.status_code == 422


def test_run_list_filters_by_recording_and_completed_status(client):
    database = client.app.state.database
    with database.session_factory() as session:
        _add_recording(session)
        _add_recording(session, rec_id="rec_other")
        _add_run(session, run_id="run_a", rec_id="rec_cmp")
        _add_run(session, run_id="run_b", rec_id="rec_cmp", status="running")
        _add_run(session, run_id="run_other", rec_id="rec_other")
        session.commit()

    response = client.get("/api/analysis-runs?recording_id=rec_cmp&status=completed")
    assert response.status_code == 200
    run_ids = [item["id"] for item in response.json()]
    assert run_ids == ["run_a"]
