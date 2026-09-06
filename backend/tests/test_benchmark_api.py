import time

import pytest

from benchmark_fixture import add_detection, add_ground_truth, add_recording, add_run


def _populate(client):
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


def _prepare(client):
    response = client.post("/api/dataset-benchmarks/prepare", json={
        "dataset_name": "SpaceNet", "dataset_split": "test", "label_space": "spacenet_14",
    })
    assert response.status_code == 200, response.text
    return response.json()


def _create(client, manifest_hash, items, allow_incomplete=False):
    response = client.post("/api/dataset-benchmarks", json={
        "name": "tiny",
        "dataset_name": "SpaceNet",
        "dataset_split": "test",
        "label_space": "spacenet_14",
        "recording_manifest_hash": manifest_hash,
        "allow_incomplete": allow_incomplete,
        "items": items,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_prepare_manifest_route(client):
    _populate(client)
    preview = _prepare(client)
    assert preview["expected_recordings"] == 2
    assert len(preview["entries"]) == 2
    assert preview["recording_manifest_hash"]
    assert preview["entries"][0]["recording_name"] == "a"


def test_resolve_runs_route(client):
    _populate(client)
    response = client.post("/api/dataset-benchmarks/resolve-runs", json={
        "dataset_name": "SpaceNet", "dataset_split": "test", "label_space": "spacenet_14",
        "pipeline_id": "pipeline_x", "pipeline_version": "1.0",
    })
    assert response.status_code == 200, response.text
    entries = {e["recording_name"]: e for e in response.json()["entries"]}
    assert entries["a"]["resolution"] == "resolved"
    assert entries["a"]["candidate_run_ids"] == ["run_a"]


def test_create_get_list_items_routes(client):
    _populate(client)
    preview = _prepare(client)
    created = _create(client, preview["recording_manifest_hash"], [
        {"recording_id": "rec_a", "analysis_run_id": "run_a"},
        {"recording_id": "rec_b", "analysis_run_id": "run_b"},
    ])
    assert created["status"] == "pending"
    assert created["coverage"] == 1.0
    assert created["comparable"] is True  # full coverage makes the evaluation comparable-capable

    listed = client.get("/api/dataset-benchmarks").json()
    assert any(item["id"] == created["id"] for item in listed)

    detail = client.get(f"/api/dataset-benchmarks/{created['id']}").json()
    assert detail["id"] == created["id"]
    assert detail["evaluation_protocol"] == "physical_tf_detection_ap_v2"

    items = client.get(f"/api/dataset-benchmarks/{created['id']}/items").json()
    assert len(items) == 2
    assert items[0]["analysis_run_id"] == "run_a"


def test_stale_manifest_rejected(client):
    _populate(client)
    response = client.post("/api/dataset-benchmarks", json={
        "name": "bad",
        "dataset_name": "SpaceNet", "dataset_split": "test", "label_space": "spacenet_14",
        "recording_manifest_hash": "0" * 64,
        "items": [{"recording_id": "rec_a", "analysis_run_id": "run_a"},
                  {"recording_id": "rec_b", "analysis_run_id": "run_b"}],
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DATASET_MANIFEST_CHANGED"


def test_run_and_retry_lifecycle_routes(client):
    _populate(client)
    preview = _prepare(client)
    created = _create(client, preview["recording_manifest_hash"], [
        {"recording_id": "rec_a", "analysis_run_id": "run_a"},
        {"recording_id": "rec_b", "analysis_run_id": "run_b"},
    ])
    run_response = client.post(f"/api/dataset-benchmarks/{created['id']}/run")
    assert run_response.status_code == 202, run_response.text

    deadline = time.time() + 30
    detail = run_response.json()
    while time.time() < deadline:
        detail = client.get(f"/api/dataset-benchmarks/{created['id']}").json()
        if detail["status"] in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.2)
    assert detail["status"] == "completed", detail
    assert detail["aggregate_metrics_json"]["localization"]["ap50"] == 1.0

    # completed cannot rerun
    retry = client.post(f"/api/dataset-benchmarks/{created['id']}/retry")
    assert retry.status_code == 409


def test_compare_route(client):
    _populate(client)
    preview = _prepare(client)
    items = [{"recording_id": "rec_a", "analysis_run_id": "run_a"},
             {"recording_id": "rec_b", "analysis_run_id": "run_b"}]
    first = _create(client, preview["recording_manifest_hash"], items)
    second = _create(client, preview["recording_manifest_hash"], items)
    for evaluation_id in (first["id"], second["id"]):
        client.post(f"/api/dataset-benchmarks/{evaluation_id}/run")
        deadline = time.time() + 30
        while time.time() < deadline:
            detail = client.get(f"/api/dataset-benchmarks/{evaluation_id}").json()
            if detail["status"] in {"completed", "failed", "interrupted"}:
                break
            time.sleep(0.2)
    response = client.post("/api/dataset-benchmarks/compare", json={
        "evaluation_a_id": first["id"], "evaluation_b_id": second["id"],
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["comparable"] is True
    assert payload["reasons"] == []
    assert payload["deltas"]["localization_ap50"] == 0.0


def test_compare_non_comparable_reports_reasons(client):
    _populate(client)
    preview = _prepare(client)
    first = _create(client, preview["recording_manifest_hash"], [
        {"recording_id": "rec_a", "analysis_run_id": "run_a"},
        {"recording_id": "rec_b", "analysis_run_id": None},
    ], allow_incomplete=True)
    second = _create(client, preview["recording_manifest_hash"], [
        {"recording_id": "rec_a", "analysis_run_id": "run_a"},
        {"recording_id": "rec_b", "analysis_run_id": "run_b"},
    ])
    response = client.post("/api/dataset-benchmarks/compare", json={
        "evaluation_a_id": first["id"], "evaluation_b_id": second["id"],
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["comparable"] is False
    assert "evaluation_a_incomplete" in payload["reasons"]
def test_tiny_end_to_end_benchmark_through_subprocess(client):
    _populate(client)
    preview = _prepare(client)
    created = _create(client, preview["recording_manifest_hash"], [
        {"recording_id": "rec_a", "analysis_run_id": "run_a"},
        {"recording_id": "rec_b", "analysis_run_id": "run_b"},
    ])
    run_response = client.post(f"/api/dataset-benchmarks/{created['id']}/run")
    assert run_response.status_code == 202, run_response.text

    deadline = time.time() + 40
    detail = run_response.json()
    while time.time() < deadline:
        detail = client.get(f"/api/dataset-benchmarks/{created['id']}").json()
        if detail["status"] in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.2)
    assert detail["status"] == "completed", detail
    assert detail["expected_recordings"] == 2
    assert detail["evaluated_recordings"] == 2
    assert detail["missing_recordings"] == 0
    assert detail["coverage"] == 1.0
    assert detail["comparable"] is True
    assert detail["evaluation_protocol"] == "physical_tf_detection_ap_v2"
    aggregate = detail["aggregate_metrics_json"]
    assert aggregate["localization"]["ap50"] == 1.0
    assert aggregate["classification_applicable"] is True
    assert aggregate["class_aware"]["map50"] == 1.0
    assert aggregate["class_aware"]["map50_95"] == 1.0

    items = client.get(f"/api/dataset-benchmarks/{created['id']}/items").json()
    assert [item["analysis_run_id"] for item in items] == ["run_a", "run_b"]

    # Create a newer run and re-fetch; membership must remain frozen to the original run.
    with client.app.state.database.session_factory() as session:
        import datetime
        add_run(session, run_id="run_a_new", recording_id="rec_a", pipeline_id="pipeline_x",
                pipeline_version="1.0", executor="imported", created_at=datetime.datetime(2026, 3, 1))
        session.commit()
    refreshed = client.get(f"/api/dataset-benchmarks/{created['id']}").json()
    assert refreshed["status"] == "completed"
    refreshed_items = client.get(f"/api/dataset-benchmarks/{created['id']}/items").json()
    assert [item["analysis_run_id"] for item in refreshed_items] == ["run_a", "run_b"]

