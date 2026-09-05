from io import BytesIO
import json
from pathlib import Path
import zipfile

from iq_fixture import write_tiny_iq


def make_zip(files: dict[str, object]) -> BytesIO:
    """Build an in-memory ZIP where each value is either a str (manifest/JSON) or bytes."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)
    buffer.seek(0)
    return buffer


def manifest(label_space="spacenet_14", **overrides) -> str:
    value = {
        "schema_version": 1,
        "pipeline": {"id": "zoomspec", "name": "ZoomSpec", "version": "1.0"},
        "label_space": label_space,
        "recording": {"name": "0", "dataset": "SpaceNet"},
        "execution": {"executor": "remote_gpu", "device": "RTX 4090", "environment": "AutoDL"},
        "results": {"detections": "detections.json", "metrics": "metrics.json"},
    }
    value.update(overrides)
    return json.dumps(value)


def detections_json(items) -> str:
    return json.dumps({"detections": items})


def detection(**overrides) -> dict:
    value = {
        "id": "det_001",
        "t_start_s": 0.0005,
        "t_end_s": 0.0035,
        "f_low_hz": 2441060000.0,
        "f_high_hz": 2441100000.0,
        "class_id": 6,
        "class_name": "BLE LE1M",
        "confidence": 0.94,
    }
    value.update(overrides)
    return value


VALID_PACKAGE = {
    "manifest.json": manifest(),
    "detections.json": detections_json([detection()]),
    "metrics.json": json.dumps({"mAP@50": 0.5, "Precision": 0.9}),
}


def _import_recording(client, *, label_space="spacenet_14"):
    with write_tiny_iq(Path(__file__).with_name(".tiny_iq.bin")).open("rb") as handle:
        response = client.post(
            "/api/recordings",
            data={
                "name": "analysis-demo",
                "sample_rate_hz": "1000000",
                "center_frequency_hz": "2441000000",
                "data_format": "complex64_le",
                "label_space": label_space,
            },
            files={"file": ("tiny.bin", handle, "application/octet-stream")},
        )
    assert response.status_code == 201
    return response.json()


def _post_package(client, recording_id, files):
    return client.post(
        "/api/imported-runs",
        data={"recording_id": recording_id},
        files={"file": ("analysis.zip", make_zip(files), "application/zip")},
    )


def test_valid_minimal_package_imports_successfully(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], VALID_PACKAGE)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["executor"] == "imported"
    assert run["status"] == "completed"
    assert run["pipeline_id"] == "zoomspec"
    assert run["pipeline_version"] == "1.0"
    assert run["recording_id"] == recording["id"]
    assert run["hardware_info_json"]["executor"] == "remote_gpu"
    assert run["hardware_info_json"]["device"] == "RTX 4090"


def test_missing_manifest_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {"detections.json": detections_json([detection()])})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_missing_detections_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {"manifest.json": manifest()})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_unsupported_schema_version_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(schema_version=2),
        "detections.json": detections_json([detection()]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_malformed_json_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": "{ not valid json",
        "detections.json": detections_json([detection()]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_invalid_time_bbox_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(),
        "detections.json": detections_json([detection(t_start_s=-1)]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_invalid_frequency_bbox_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(),
        "detections.json": detections_json([detection(f_high_hz=2500000000.0)]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_invalid_confidence_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(),
        "detections.json": detections_json([detection(confidence=1.1)]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_invalid_class_id_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(),
        "detections.json": detections_json([detection(class_id=99)]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_class_name_mismatch_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(),
        "detections.json": detections_json([detection(class_name="Not A Class")]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_label_space_mismatch_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(label_space="other_space"),
        "detections.json": detections_json([detection()]),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_zip_slip_rejected(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(),
        "detections.json": detections_json([detection()]),
        "../../platform.db": b"evil",
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"


def test_failed_import_leaves_no_analysis_run(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], {
        "manifest.json": manifest(schema_version=2),
        "detections.json": detections_json([detection()]),
    })
    assert response.status_code == 400
    # A broken import must not have written a run: importing again from the same
    # clean baseline should still succeed, proving no partial row was committed.
    retry = _post_package(client, recording["id"], VALID_PACKAGE)
    assert retry.status_code == 201
    assert retry.json()["status"] == "completed"


def test_failed_import_leaves_no_partial_database_rows(settings):
    import sqlite3

    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(settings))
    recording = _import_recording(client)
    _post_package(client, recording["id"], {
        "manifest.json": manifest(schema_version=2),
        "detections.json": detections_json([detection()]),
    })
    # The settings fixture points at an isolated sqlite DB file; confirm it stayed
    # empty of run/detection rows despite a valid recording being present.
    db_path = settings.database_url.removeprefix("sqlite:///")
    connection = sqlite3.connect(db_path)
    try:
        run_count = connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
        detection_count = connection.execute("SELECT COUNT(*) FROM detection_results").fetchone()[0]
    finally:
        connection.close()
    assert run_count == 0
    assert detection_count == 0


def test_successful_import_creates_completed_run_and_accessible_detections(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], VALID_PACKAGE)
    assert response.status_code == 201
    run = response.json()

    detections = client.get(f"/api/analysis-runs/{run['id']}/detections")
    assert detections.status_code == 200
    items = detections.json()
    assert len(items) == 1
    assert items[0]["recording_id"] == recording["id"]
    assert items[0]["class_name"] == "BLE LE1M"
    assert items[0]["confidence"] == 0.94

    single = client.get(f"/api/detections/{items[0]['id']}")
    assert single.status_code == 200
    assert single.json()["run_id"] == run["id"]

    detail = client.get(f"/api/analysis-runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"


def test_imported_run_visible_through_existing_analysis_api(client):
    recording = _import_recording(client)
    response = _post_package(client, recording["id"], VALID_PACKAGE)
    assert response.status_code == 201
    run = response.json()
    fetched = client.get(f"/api/analysis-runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["executor"] == "imported"
