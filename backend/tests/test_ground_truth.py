from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tiny_iq_complex64.bin"
GT_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tiny_ground_truth.json"


def _import_recording(client, *, label_space="spacenet_14"):
    with FIXTURE.open("rb") as handle:
        response = client.post(
            "/api/recordings",
            data={
                "name": "gt-demo",
                "sample_rate_hz": "1000000",
                "center_frequency_hz": "2441000000",
                "data_format": "complex64_le",
                "label_space": label_space,
            },
            files={"file": ("tiny.bin", handle, "application/octet-stream")},
        )
    assert response.status_code == 201
    return response.json()


def _valid_object(recording):
    return {
        "id": "gt_boundary",
        "t_start_s": 0.0,
        "t_end_s": recording["duration_s"],
        "f_low_hz": 2440900000.0,
        "f_high_hz": 2441100000.0,
        "class_id": 9,
        "class_name": "LoRa 250kHz",
    }


def test_ground_truth_import_and_read_allows_recording_time_boundaries(client):
    recording = _import_recording(client)
    response = client.post(
        f"/api/recordings/{recording['id']}/ground-truth",
        json={"label_space": "spacenet_14", "objects": [_valid_object(recording)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["t_start_s"] == 0.0
    assert response.json()[0]["t_end_s"] == recording["duration_s"]

    listing = client.get(f"/api/recordings/{recording['id']}/ground-truth")
    assert listing.status_code == 200
    assert listing.json()[0]["class_name"] == "LoRa 250kHz"

    detail = client.get(f"/api/recordings/{recording['id']}")
    assert detail.json()["has_ground_truth"] is True


def test_ground_truth_rejects_invalid_time_order(client):
    recording = _import_recording(client)
    item = _valid_object(recording)
    item["t_start_s"] = item["t_end_s"]
    response = client.post(
        f"/api/recordings/{recording['id']}/ground-truth",
        json={"label_space": "spacenet_14", "objects": [item]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_GROUND_TRUTH"


def test_ground_truth_rejects_invalid_frequency_order(client):
    recording = _import_recording(client)
    item = _valid_object(recording)
    item["f_low_hz"] = item["f_high_hz"]
    response = client.post(
        f"/api/recordings/{recording['id']}/ground-truth",
        json={"label_space": "spacenet_14", "objects": [item]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_GROUND_TRUTH"


def test_ground_truth_rejects_class_mapping_mismatch(client):
    recording = _import_recording(client)
    item = _valid_object(recording)
    item["class_name"] = "BLE LE1M"
    response = client.post(
        f"/api/recordings/{recording['id']}/ground-truth",
        json={"label_space": "spacenet_14", "objects": [item]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_GROUND_TRUTH"


def test_ground_truth_rejects_box_outside_recording_extent(client):
    recording = _import_recording(client)
    item = _valid_object(recording)
    item["f_high_hz"] = recording["frequency_high_hz"] + 1
    response = client.post(
        f"/api/recordings/{recording['id']}/ground-truth",
        json={"label_space": "spacenet_14", "objects": [item]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_GROUND_TRUTH"
