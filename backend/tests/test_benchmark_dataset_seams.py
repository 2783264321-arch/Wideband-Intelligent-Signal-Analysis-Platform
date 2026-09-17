"""Focused tests for P3B product seams on dataset benchmark evaluation.

Covers: dataset_id list filtering, dataset_id on read, dataset-membership
compatibility on compare, and sample-level recording drill-down rows.
"""

from benchmark_fixture import add_detection, add_ground_truth, add_recording, add_run

from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.benchmarks.worker import execute_benchmark


def _populate(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_recording(session, recording_id="rec_b", name="b")
        add_ground_truth(session, gt_id="gt_a", recording_id="rec_a", class_id=9, class_name="LoRa 250kHz",
                         t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_ground_truth(session, gt_id="gt_b", recording_id="rec_b", class_id=6, class_name="BLE LE1M",
                         t0=0.03, t1=0.04, f0=2_440_800_000.0, f1=2_440_900_000.0)
        for run_id in ("run_a", "run_a2"):
            add_run(session, run_id=run_id, recording_id="rec_a", pipeline_id="pipeline_x",
                    pipeline_version="1.0", executor="imported")
        for run_id in ("run_b", "run_b2"):
            add_run(session, run_id=run_id, recording_id="rec_b", pipeline_id="pipeline_x",
                    pipeline_version="1.0", executor="imported")
        add_detection(session, detection_id="det_a", run_id="run_a", class_id=9, class_name="LoRa 250kHz",
                      confidence=0.9, t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_detection(session, detection_id="det_b", run_id="run_b", class_id=6, class_name="BLE LE1M",
                      confidence=0.8, t0=0.03, t1=0.04, f0=2_440_800_000.0, f1=2_440_900_000.0)
        add_detection(session, detection_id="det_a2", run_id="run_a2", class_id=9, class_name="LoRa 250kHz",
                      confidence=0.7, t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_detection(session, detection_id="det_b2", run_id="run_b2", class_id=6, class_name="BLE LE1M",
                      confidence=0.7, t0=0.03, t1=0.04, f0=2_440_800_000.0, f1=2_440_900_000.0)
        session.commit()


def _items(run_a="run_a", run_b="run_b"):
    return [
        {"recording_id": "rec_a", "analysis_run_id": run_a},
        {"recording_id": "rec_b", "analysis_run_id": run_b},
    ]


def _create(client, items, name="tiny", allow_incomplete=False):
    database = client.app.state.database
    with database.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        evaluation = svc.create_evaluation(
            name=name,
            dataset_name="SpaceNet",
            dataset_split="test",
            label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=items,
            allow_incomplete=allow_incomplete,
        )
        return evaluation.id


def _set_dataset_id(client, evaluation_id, dataset_id):
    database = client.app.state.database
    with database.session_factory() as session:
        evaluation = session.get(DatasetEvaluationModel, evaluation_id)
        evaluation.dataset_id = dataset_id
        session.commit()


def _run(client, settings, evaluation_id):
    execute_benchmark(evaluation_id, settings)


def _compare(client, a_id, b_id):
    response = client.post("/api/dataset-benchmarks/compare",
                           json={"evaluation_a_id": a_id, "evaluation_b_id": b_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_list_evaluations_filters_by_dataset_id(client, settings):
    _populate(client)
    first = _create(client, _items(), name="first")
    second = _create(client, _items(), name="second")
    _set_dataset_id(client, first, "ds_a")
    _set_dataset_id(client, second, "ds_b")

    unfiltered = client.get("/api/dataset-benchmarks")
    assert unfiltered.status_code == 200
    assert {row["id"] for row in unfiltered.json()} >= {first, second}

    filtered = client.get("/api/dataset-benchmarks", params={"dataset_id": "ds_a"})
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()] == [first]


def test_evaluation_read_exposes_dataset_id(client, settings):
    _populate(client)
    evaluation_id = _create(client, _items(), name="one")
    _set_dataset_id(client, evaluation_id, "ds_a")

    detail = client.get(f"/api/dataset-benchmarks/{evaluation_id}")
    assert detail.status_code == 200
    assert detail.json()["dataset_id"] == "ds_a"


def test_compare_same_dataset_exposes_recording_rows(client, settings):
    _populate(client)
    first = _create(client, _items(), name="A")
    second = _create(client, _items(run_a="run_a2", run_b="run_b2"), name="B")
    _set_dataset_id(client, first, "ds_a")
    _set_dataset_id(client, second, "ds_a")
    _run(client, settings, first)
    _run(client, settings, second)

    payload = _compare(client, first, second)
    assert payload["comparable"] is True
    assert payload["reasons"] == []
    assert payload["aggregate_a"]["localization"]["ap50"] == 1.0
    assert payload["aggregate_b"]["localization"]["ap50"] == 1.0
    assert payload["deltas"]["localization_ap50"] == 0.0

    rows = {row["recording_id"]: row for row in payload["recordings"]}
    assert rows["rec_a"] == {
        "recording_id": "rec_a",
        "recording_name": "a",
        "evaluation_a_run_id": "run_a",
        "evaluation_b_run_id": "run_a2",
        "comparison": "both_detected",
    }
    assert rows["rec_b"]["recording_name"] == "b"
    assert rows["rec_b"]["comparison"] == "both_detected"


def test_compare_incompatible_dataset_rejected(client, settings):
    _populate(client)
    first = _create(client, _items(), name="A")
    second = _create(client, _items(), name="B")
    _set_dataset_id(client, first, "ds_a")
    _set_dataset_id(client, second, "ds_b")
    _run(client, settings, first)
    _run(client, settings, second)

    payload = _compare(client, first, second)
    assert payload["comparable"] is False
    assert "dataset_id_mismatch" in payload["reasons"]


def test_compare_legacy_evaluations_without_dataset_id_still_compatible(client, settings):
    _populate(client)
    first = _create(client, _items(), name="A")
    second = _create(client, _items(), name="B")
    _run(client, settings, first)
    _run(client, settings, second)

    payload = _compare(client, first, second)
    assert payload["comparable"] is True
    assert "dataset_id_mismatch" not in payload["reasons"]


def test_compare_sample_case_classification(client, settings):
    database = client.app.state.database
    with database.session_factory() as session:
        for index in (1, 2, 3, 4):
            add_recording(session, recording_id=f"rec_{index}", name=f"r{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=f"rec_{index}", class_id=9,
                             class_name="LoRa 250kHz",
                             t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
            for prefix in ("a", "b"):
                add_run(session, run_id=f"run_{prefix}_{index}", recording_id=f"rec_{index}",
                        pipeline_id="pipeline_x", pipeline_version="1.0", executor="imported")
        for index in (1, 2):
            add_detection(session, detection_id=f"det_a_{index}", run_id=f"run_a_{index}", class_id=9,
                          class_name="LoRa 250kHz", confidence=0.9,
                          t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        for index in (1, 3):
            add_detection(session, detection_id=f"det_b_{index}", run_id=f"run_b_{index}", class_id=9,
                          class_name="LoRa 250kHz", confidence=0.9,
                          t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()

    items_a = [{"recording_id": f"rec_{i}", "analysis_run_id": f"run_a_{i}"} for i in (1, 2, 3, 4)]
    items_b = [{"recording_id": f"rec_{i}", "analysis_run_id": f"run_b_{i}"} for i in (1, 2, 3, 4)]
    first = _create(client, items_a, name="A")
    second = _create(client, items_b, name="B")
    _run(client, settings, first)
    _run(client, settings, second)

    payload = _compare(client, first, second)
    rows = {row["recording_id"]: row["comparison"] for row in payload["recordings"]}
    assert rows == {
        "rec_1": "both_detected",
        "rec_2": "a_only",
        "rec_3": "b_only",
        "rec_4": "both_missed",
    }
