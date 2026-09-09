"""Coordinator completion-phase terminalization (Task 12F-C corrective).

If the remote batch is completed but the LOCAL completion phase fails
(download / envelope / writer / ingest), the run must be audited as
``interrupted`` in the DB (error_type + finished_at), never left pending/running.
Tests use the real Coordinator + real DB model with a fenced run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace

from benchmark_fixture import add_recording

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.pipelines.base import DetectionPayload, PipelineOutput
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator
from app.remote_execution.package_publisher import build_analysis_package_zip
from app.remote_execution.request_builder import freeze_request_provenance
from app.remote_execution.result_publisher import publish_result
from app.remote_execution.schema import RemoteBatchStatusV1, RemoteItemStatusV1

RUN = "a" * 40
MANIFEST = "b" * 64
RECORDING_ID = "rec_cf"
PIPELINE_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
PIPELINE_VERSION = "1.0.0"


def _metadata(run_id="run_x", token="tok_x"):
    m = freeze_request_provenance(
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
    m["coordinator_token"] = token
    return m


def _seed(client, metadata, run_id="run_x", status="running"):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=RECORDING_ID, name="0",
                      dataset_name="SpaceNet", dataset_split="test",
                      label_space="spacenet_14")
        session.get(RecordingModel, RECORDING_ID).source_data_sha256 = "1" * 64
        session.add(AnalysisRunModel(
            id=run_id, recording_id=RECORDING_ID, pipeline_id=PIPELINE_ID,
            pipeline_version=PIPELINE_VERSION, executor="remote_gpu",
            status=status, parameters_json={}, execution_metadata_json=metadata,
        ))
        session.commit()


class CompletedJobManager:
    def __init__(self, *, download_error=None, materialize=False):
        self.download_error = download_error
        self.materialize = materialize
        self.submits = []

    def submit(self, batch, request_json_path):
        self.submits.append(batch)

    def status(self, batch_id):
        return RemoteBatchStatusV1(
            batch_id=batch_id, status="completed",
            items=[RemoteItemStatusV1(item_key="ik", status="completed")],
        )

    def download(self, batch_id, item_key, dest_dir):
        if self.download_error is not None:
            raise self.download_error
        dest = Path(dest_dir) / item_key
        dest.mkdir(parents=True, exist_ok=True)
        if self.materialize:
            _materialize_real(batch_id, item_key, dest)
        return dest


def _materialize_real(batch_id, item_key, dest):
    batch = None  # envelope identity not verified by the fake ingest
    scratch = Path(tempfile.mkdtemp(prefix="cf_terminal_"))
    item = SimpleNamespace(
        item_key=item_key, request_id="req_1", local_run_id="run_x",
        orchestrator_commit=RUN,
        recording=SimpleNamespace(
            expected_recording_fingerprint="2" * 64,
            expected_source_data_sha256="1" * 64,
            dataset_key="0", dataset_name="SpaceNet",
        ),
    )
    batch = SimpleNamespace(
        batch_id=batch_id,
        required_remote_runtime_commit=RUN,
        asset_manifest_sha256=MANIFEST,
        pipeline=SimpleNamespace(id=PIPELINE_ID, version=PIPELINE_VERSION),
    )
    output = PipelineOutput(detections=[
        DetectionPayload(t_start_s=0.01, t_end_s=0.02, f_low_hz=2440600000.0,
                         f_high_hz=2440700000.0, class_id=9,
                         class_name="LoRa 250kHz", confidence=0.94),
    ])
    zip_path = build_analysis_package_zip(output, "0", "SpaceNet", scratch / "work")
    publish_result(
        job_root=scratch, item=item, batch=batch, zip_path=zip_path,
        remote_runtime_commit=RUN, asset_manifest_sha256=MANIFEST,
        hardware={"device": 0},
        remote_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        remote_finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )
    result_dir = scratch / "results" / item_key
    (dest / "envelope.json").write_bytes((result_dir / "envelope.json").read_bytes())
    (dest / "analysis_result.zip").write_bytes((result_dir / "analysis_result.zip").read_bytes())


def _coordinator(client, job_manager, metadata, *, ingest=None, writer=None, writer_factory=None):
    return Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=job_manager,
        metadata=metadata,
        coordinator_token=metadata["coordinator_token"],
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        ingest=ingest,
        writer=writer,
        writer_factory=writer_factory,
    )


def _run_state(client, run_id="run_x"):
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, run_id)
        return run.status, run.error_type, run.error_message, run.finished_at is not None


def test_completed_download_failure_terminalizes_run(client):
    metadata = _metadata()
    _seed(client, metadata)
    jm = CompletedJobManager(download_error=PlatformError("REMOTE_DOWNLOAD_FAILED", "Remote result download failed."))

    result = _coordinator(client, jm, metadata).run()

    assert result == "interrupted"
    status, error_type, error_message, finished = _run_state(client)
    assert status == "interrupted"
    assert error_type == "REMOTE_DOWNLOAD_FAILED"
    assert error_message is not None
    assert finished is True


def test_completed_ingest_failure_terminalizes_run(client):
    metadata = _metadata()
    _seed(client, metadata)
    jm = CompletedJobManager(materialize=True)

    def failing_ingest(session, run, envelope, zip_path, writer):
        raise PlatformError("REMOTE_RESULT_INVALID", "Remote result envelope is invalid.")

    result = _coordinator(client, jm, metadata, ingest=failing_ingest,
                          writer_factory=lambda session, run: SimpleNamespace()).run()

    assert result == "interrupted"
    status, error_type, error_message, finished = _run_state(client)
    assert status == "interrupted"
    assert error_type == "REMOTE_RESULT_INVALID"
    assert error_message is not None
    assert finished is True


def test_completed_missing_writer_terminalizes_run(client):
    metadata = _metadata()
    _seed(client, metadata)
    jm = CompletedJobManager(materialize=True)
    ingestor = SimpleNamespace(calls=[])

    def recording_ingest(session, run, envelope, zip_path, writer):
        ingestor.calls.append(1)
        return envelope.payload_sha256

    result = _coordinator(client, jm, metadata, ingest=recording_ingest, writer=None).run()

    assert result == "interrupted"
    assert ingestor.calls == []
    status, error_type, error_message, finished = _run_state(client)
    assert status == "interrupted"
    assert error_type is not None
    assert error_message is not None
    assert finished is True


def test_completed_unexpected_exception_fails_closed(client):
    metadata = _metadata()
    _seed(client, metadata)
    jm = CompletedJobManager(materialize=True)

    def exploding_ingest(session, run, envelope, zip_path, writer):
        raise RuntimeError("internal explosion with secrets")

    result = _coordinator(client, jm, metadata, ingest=exploding_ingest,
                          writer_factory=lambda session, run: SimpleNamespace()).run()

    assert result == "interrupted"
    status, error_type, error_message, finished = _run_state(client)
    assert status == "interrupted"
    assert error_type is not None
    assert error_message is not None
    assert "secrets" not in (error_message or "")
    assert "Traceback" not in (error_message or "")
    assert finished is True