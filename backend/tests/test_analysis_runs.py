from pathlib import Path
import time

from iq_fixture import write_tiny_iq

from app.pipelines.base import RecordingInput
from app.pipelines.dummy import DummyPipeline
from app.pipelines.registry import PipelineRegistry



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


def test_dummy_pipeline_satisfies_contract_and_returns_physical_detection(tmp_path):
    pipeline = DummyPipeline()
    assert pipeline.definition.id == "dummy"
    assert pipeline.definition.cpu_supported is True
    registry = PipelineRegistry([pipeline])
    assert registry.get("dummy") is pipeline
    assert [item.id for item in registry.list()] == ["dummy"]

    recording = RecordingInput(
        id="rec_contract",
        data_path=tmp_path / "raw.iq",
        data_format="complex64_le",
        sample_rate_hz=1_000_000,
        center_frequency_hz=2_441_000_000,
        frequency_low_hz=2_440_500_000,
        frequency_high_hz=2_441_500_000,
        duration_s=1.0,
        label_space="spacenet_14",
    )
    output = pipeline.run(recording, parameters={}, workspace=tmp_path)
    assert output.detections
    item = output.detections[0]
    assert 0 <= item.t_start_s < item.t_end_s <= recording.duration_s
    assert recording.frequency_low_hz <= item.f_low_hz < item.f_high_hz <= recording.frequency_high_hz


def test_analysis_run_executes_dummy_pipeline_in_subprocess_and_persists_results(client):
    recording = _import_recording(client)
    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": recording["id"],
            "pipeline_id": "dummy",
            "executor": "local_cpu",
            "parameters": {},
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] in {"pending", "running"}
    assert run["worker_pid"] is not None

    deadline = time.time() + 10
    while time.time() < deadline:
        status = client.get(f"/api/analysis-runs/{run['id']}")
        assert status.status_code == 200
        run = status.json()
        if run["status"] in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.1)

    assert run["status"] == "completed", run
    detections = client.get(f"/api/analysis-runs/{run['id']}/detections")
    assert detections.status_code == 200
    assert len(detections.json()) >= 1
    assert detections.json()[0]["recording_id"] == recording["id"]


def test_analysis_run_rejects_unknown_pipeline_as_business_error(client):
    recording = _import_recording(client)
    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": recording["id"],
            "pipeline_id": "missing-pipeline",
            "executor": "local_cpu",
            "parameters": {},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PIPELINE_INCOMPATIBLE"


def test_app_startup_marks_stale_running_run_interrupted(settings):
    from fastapi.testclient import TestClient

    from app.analysis.model import AnalysisRunModel
    from app.db.base import Base, load_domain_models
    from app.db.session import Database
    from app.main import create_app
    from app.recordings.model import RecordingModel

    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    with database.session_factory() as session:
        recording = RecordingModel(
            id="rec_stale",
            name="stale-recording",
            data_path="recordings/rec_stale/raw.iq",
            data_format="complex64_le",
            sample_rate_hz=1_000_000.0,
            center_frequency_hz=2_441_000_000.0,
            frequency_low_hz=2_440_500_000.0,
            frequency_high_hz=2_441_500_000.0,
            num_samples=4096,
            duration_s=0.004096,
            dataset_name=None,
            dataset_split=None,
            label_space="spacenet_14",
            has_ground_truth=False,
        )
        run = AnalysisRunModel(
            id="run_stale",
            recording_id=recording.id,
            pipeline_id="dummy",
            pipeline_version="1.0",
            executor="local_cpu",
            status="running",
            parameters_json={},
            worker_pid=999999,
        )
        session.add_all([recording, run])
        session.commit()

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/analysis-runs/run_stale")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "interrupted"
        assert payload["error_type"] == "ANALYSIS_INTERRUPTED"
        assert "previous local analysis" in payload["error_message"].lower()


def test_business_error_response_has_stable_shape(client):
    response = client.get("/api/detections/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "DETECTION_NOT_FOUND",
            "message": "Detection result was not found.",
            "details": {},
        }
    }
