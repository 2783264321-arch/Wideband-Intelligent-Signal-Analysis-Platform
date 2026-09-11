"""Full remote live-loop coverage (Task 12F-C Task 1, CPU/no-GPU).

Drives the REAL Coordinator loop to ``completed`` using the REAL
``ingest_remote_result`` + ``AnalysisResultWriter`` and a REAL valid Analysis
Package v1 envelope+ZIP materialized by the fake transport's ``download``.
Then verifies SAME-run completion, detection persistence + GET readback,
Algorithm Lab comparison with TWO DISTINCT completed runs, and write-once
behavior. No SSH/GPU.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from benchmark_fixture import add_detection, add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.pipelines.base import DetectionPayload, PipelineOutput
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator, make_production_writer_factory
from app.remote_execution.package_publisher import build_analysis_package_zip
from app.remote_execution.request_builder import freeze_request_provenance
from app.remote_execution.result_publisher import publish_result
from app.remote_execution.runtime import RuntimeDescriptor
from app.remote_execution.schema import RemoteBatchStatusV1, RemoteItemStatusV1

RUN = "a" * 40
MANIFEST = "b" * 64
RECORDING_ID = "rec_sn"
PIPELINE_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
PIPELINE_VERSION = "1.0.0"
HARDWARE = {"device_index": 0, "device_type": "cuda", "device_name": "Fake GPU"}

DET_1 = dict(t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0,
             class_id=9, class_name="LoRa 250kHz", confidence=0.94,
             scores={"cpn": 0.9, "frn_signal": 0.92, "frn_class": 0.95, "fused": 0.94})
DET_2 = dict(t0=0.03, t1=0.04, f0=2440800000.0, f1=2440900000.0,
             class_id=6, class_name="BLE LE1M", confidence=0.61, scores=None)


def _to_payload(detection):
    return DetectionPayload(
        t_start_s=detection["t0"], t_end_s=detection["t1"],
        f_low_hz=detection["f0"], f_high_hz=detection["f1"],
        class_id=detection["class_id"], class_name=detection["class_name"],
        confidence=detection["confidence"], scores=detection["scores"],
    )


def _batch_metadata(run_id="run_a", token="tok_a"):
    metadata = freeze_request_provenance(
        local_run_id=run_id,
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id=PIPELINE_ID,
        pipeline_version=PIPELINE_VERSION,
        required_remote_runtime_commit=RUN,
        orchestrator_commit=RUN,
        asset_manifest_sha256=MANIFEST,
        remote_profile="autodl_primary",
    )
    metadata["coordinator_token"] = token
    return metadata


def _add_detection_payload(session, run_id, detection):
    add_detection(
        session,
        detection_id=f"det_{run_id}_{detection['class_id']}",
        run_id=run_id,
        class_id=detection["class_id"],
        class_name=detection["class_name"],
        confidence=detection["confidence"],
        t0=detection["t0"], t1=detection["t1"],
        f0=detection["f0"], f1=detection["f1"],
    )


def _seed_recording_and_gt(client, recording_id=RECORDING_ID):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=recording_id, name="0",
                      dataset_name="SpaceNet", dataset_split="test",
                      label_space="spacenet_14")
        recording = session.get(RecordingModel, recording_id)
        recording.source_data_sha256 = "1" * 64
        add_ground_truth(session, gt_id="gt_1", recording_id=recording_id,
                         class_id=9, class_name="LoRa 250kHz",
                         t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0)
        add_ground_truth(session, gt_id="gt_2", recording_id=recording_id,
                         class_id=6, class_name="BLE LE1M",
                         t0=0.05, t1=0.06, f0=2440800000.0, f1=2440900000.0)
        session.commit()


def _seed_remote_run(client, metadata, run_id="run_a", status="pending"):
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id=run_id,
            recording_id=RECORDING_ID,
            pipeline_id=PIPELINE_ID,
            pipeline_version=PIPELINE_VERSION,
            executor="remote_gpu",
            status=status,
            parameters_json={},
            execution_metadata_json=metadata,
        ))
        session.commit()


class MaterializingJobManager:
    """submit materializes a REAL envelope+ZIP via publish_result; status reports
    completed; download replays the materialized terminal files."""

    def __init__(self, detections):
        self.detections = detections
        self.submits = []
        self._envelope_bytes = None
        self._zip_bytes = None

    def submit(self, batch, request_json_path):
        self.submits.append(batch)
        item = batch.items[0]
        scratch = Path(tempfile.mkdtemp(prefix="live_loop_terminal_"))
        output = PipelineOutput(detections=[_to_payload(d) for d in self.detections])
        zip_path = build_analysis_package_zip(
            output,
            pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
            label_space="spacenet_14",
            runtime_descriptor=RuntimeDescriptor("remote_gpu", "cuda", 0, "float16"),
            parameters={},
            recording_name=item.recording.dataset_key,
            dataset_name=item.recording.dataset_name,
            workspace=scratch / "work",
        )
        publish_result(
            job_root=scratch,
            item=item,
            batch=batch,
            zip_path=zip_path,
            remote_runtime_commit=batch.required_remote_runtime_commit,
            asset_manifest_sha256=batch.asset_manifest_sha256,
            hardware=dict(HARDWARE),
            remote_started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            remote_finished_at=datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc),
        )
        result_dir = scratch / "results" / item.item_key
        self._envelope_bytes = (result_dir / "envelope.json").read_bytes()
        self._zip_bytes = (result_dir / "analysis_result.zip").read_bytes()

    def status(self, batch_id):
        return RemoteBatchStatusV1(
            batch_id=batch_id,
            status="completed",
            items=[RemoteItemStatusV1(item_key="ik", status="completed")],
        )

    def download(self, batch_id, item_key, dest_dir):
        dest = Path(dest_dir) / item_key
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "envelope.json").write_bytes(self._envelope_bytes)
        (dest / "analysis_result.zip").write_bytes(self._zip_bytes)
        return dest


def _coordinator(client, settings, job_manager, metadata):
    return Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=job_manager,
        metadata=metadata,
        coordinator_token=metadata["coordinator_token"],
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        writer_factory=make_production_writer_factory(settings),
    )


def _detection_rows(client, run_id):
    with client.app.state.database.session_factory() as session:
        return list(session.query(DetectionResultModel)
                    .filter(DetectionResultModel.run_id == run_id).all())


def test_live_loop_remote_run_completes_and_persists(client, settings):
    _seed_recording_and_gt(client)
    metadata = _batch_metadata(run_id="run_a", token="tok_a")
    _seed_remote_run(client, metadata)
    job_manager = MaterializingJobManager([DET_1, DET_2])

    result = _coordinator(client, settings, job_manager, metadata).run()

    assert result == "completed"
    assert len(job_manager.submits) == 1
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_a")
        assert run.status == "completed"
        assert run.hardware_info_json == HARDWARE
        assert run.finished_at is not None
    rows = _detection_rows(client, "run_a")
    assert len(rows) == 2
    for row, exp in zip(rows, [DET_1, DET_2]):
        assert row.t_start_s == exp["t0"]
        assert row.t_end_s == exp["t1"]
        assert row.f_low_hz == exp["f0"]
        assert row.f_high_hz == exp["f1"]
        assert row.class_id == exp["class_id"]
        assert row.class_name == exp["class_name"]
        assert row.confidence == exp["confidence"]
        assert row.scores_json == exp["scores"]


def test_live_loop_get_readback_returns_detections(client, settings):
    _seed_recording_and_gt(client)
    metadata = _batch_metadata(run_id="run_a", token="tok_a")
    _seed_remote_run(client, metadata)
    _coordinator(client, settings, MaterializingJobManager([DET_1]), metadata).run()

    run_resp = client.get("/api/analysis-runs/run_a")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["status"] == "completed"
    assert body["executor"] == "remote_gpu"
    assert body["hardware_info_json"] == HARDWARE
    meta = body["execution_metadata_json"] or {}
    assert meta["remote_profile"] == "autodl_primary"
    assert meta["required_remote_runtime_commit"]
    assert "coordinator_token" not in meta
    assert "request_id" not in meta
    assert "batch_id" not in meta
    assert "request_sha256" not in meta

    det_resp = client.get("/api/analysis-runs/run_a/detections")
    assert det_resp.status_code == 200
    detections = det_resp.json()
    assert len(detections) == 1
    assert detections[0]["class_name"] == "LoRa 250kHz"
    assert detections[0]["confidence"] == 0.94


def test_live_loop_algorithm_lab_compares_distinct_runs(client, settings):
    _seed_recording_and_gt(client)
    # run A = completed via the REAL remote live loop.
    metadata = _batch_metadata(run_id="run_a", token="tok_a")
    _seed_remote_run(client, metadata)
    _coordinator(client, settings, MaterializingJobManager([DET_1, DET_2]), metadata).run()
    # run B = a second DISTINCT completed run on the same recording.
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_b", recording_id=RECORDING_ID, pipeline_id=PIPELINE_ID,
            pipeline_version=PIPELINE_VERSION, executor="local_cpu",
            status="completed", parameters_json={},
        ))
        _add_detection_payload(session, "run_b", DET_2)
        session.commit()

    response = client.post("/api/algorithm-lab/compare", json={
        "recording_id": RECORDING_ID,
        "run_a_id": "run_a",
        "run_b_id": "run_b",
        "iou_threshold": 0.5,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["run_a"]["run_id"] == "run_a"
    assert body["run_b"]["run_id"] == "run_b"
    assert body["run_a"]["metrics"] is not None
    assert body["run_a"]["classification"] is not None


def test_live_loop_write_once_no_duplicate_rows(client, settings):
    _seed_recording_and_gt(client)
    metadata = _batch_metadata(run_id="run_a", token="tok_a")
    _seed_remote_run(client, metadata)
    job_manager = MaterializingJobManager([DET_1, DET_2])
    coord = _coordinator(client, settings, job_manager, metadata)

    assert coord.run() == "completed"
    assert len(_detection_rows(client, "run_a")) == 2

    coord_again = _coordinator(client, settings, job_manager, metadata)
    assert coord_again.run() == "completed"
    assert len(job_manager.submits) == 1
    assert len(_detection_rows(client, "run_a")) == 2