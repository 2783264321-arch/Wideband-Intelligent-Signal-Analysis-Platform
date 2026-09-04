from pathlib import Path

import numpy as np

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tiny_iq_complex64.bin"


def _import_recording(client):
    with FIXTURE.open("rb") as handle:
        response = client.post(
            "/api/recordings",
            data={
                "name": "detection-demo",
                "sample_rate_hz": "1000000",
                "center_frequency_hz": "2441000000",
                "data_format": "complex64_le",
                "label_space": "spacenet_14",
            },
            files={"file": ("tiny.bin", handle, "application/octet-stream")},
        )
    assert response.status_code == 201
    return response.json()


def _seed_detection(client, recording_id: str):
    with client.app.state.database.session_factory() as session:
        run = AnalysisRunModel(
            id="run_demo",
            recording_id=recording_id,
            pipeline_id="fixture",
            pipeline_version="1.0",
            executor="imported",
            status="completed",
            parameters_json={},
        )
        detection = DetectionResultModel(
            id="det_demo",
            run_id=run.id,
            t_start_s=0.0005,
            t_end_s=0.0035,
            f_low_hz=2441060000.0,
            f_high_hz=2441100000.0,
            class_id=6,
            class_name="BLE LE1M",
            confidence=0.94,
            scores_json={"classification": 0.94},
        )
        session.add_all([run, detection])
        session.commit()


def test_detection_read_apis_return_persisted_physical_results(client):
    recording = _import_recording(client)
    _seed_detection(client, recording["id"])

    listing = client.get("/api/analysis-runs/run_demo/detections")
    assert listing.status_code == 200
    assert listing.json()[0]["f_low_hz"] == 2441060000.0
    assert listing.json()[0]["confidence"] == 0.94

    detail = client.get("/api/detections/det_demo")
    assert detail.status_code == 200
    assert detail.json()["class_name"] == "BLE LE1M"
    assert detail.json()["run_id"] == "run_demo"


def test_waveform_endpoint_returns_display_sized_i_and_q_arrays(client):
    recording = _import_recording(client)
    response = client.get(
        f"/api/recordings/{recording['id']}/waveform",
        params={"t_start_s": 0.0, "t_end_s": recording["duration_s"], "max_points": 128},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["time_s"]) <= 128
    assert len(payload["time_s"]) == len(payload["i"]) == len(payload["q"])
    assert payload["time_s"][0] == 0.0


def test_fft_endpoint_returns_display_sized_absolute_frequency_axis(client):
    recording = _import_recording(client)
    _seed_detection(client, recording["id"])

    response = client.get("/api/detections/det_demo/fft", params={"max_points": 256})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["frequency_hz"]) <= 256
    assert len(payload["frequency_hz"]) == len(payload["magnitude_db"])
    axis = np.asarray(payload["frequency_hz"])
    assert axis.min() >= recording["frequency_low_hz"]
    assert axis.max() <= recording["frequency_high_hz"]
