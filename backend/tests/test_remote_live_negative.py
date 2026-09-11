"""Live negative / unavailable semantics (Task 12F-C Task 2, CPU/no-GPU).

Covers: probe-unavailable create_run rejection; missing scientific mapping
mapped to transport-unavailable at the HTTP availability endpoint; runtime-drift
submit as an uncertain submit that reconciles the SAME batch and never spawns
the frozen request under a new runtime (completes when a matching remote job
exists, fails closed when it does not); and unavailable deployment never
corrupting an existing completed run. No SSH/GPU.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import subprocess
from types import SimpleNamespace

from benchmark_fixture import add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator
from app.remote_execution.executor import SshRemoteExecutorProbe
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.schema import RemoteBatchStatusV1, RemoteItemStatusV1
from app.remote_execution.transport import SshRunner

from executor_fixtures import FakeProvider, FakeRegistry

RUN = "a" * 40
MANIFEST = "b" * 64
PIPELINE_ID = ZOOMSPEC_FROZEN_DEFINITION.id


class FakeProbe:
    def __init__(self, *, available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE"):
        self.available = available
        self.reason_code = reason_code
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        self.calls.append((recording.id, pipeline.id))
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=self.available,
            reason_code=None if self.available else self.reason_code,
            reason_message=None if self.available else "unavailable",
            remote_profile="autodl_primary",
            recommended=False,
        )


def _add_sn_recording(client, recording_id="rec_sn", name="0"):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=recording_id, name=name,
                      dataset_name="SpaceNet", dataset_split="test",
                      label_space="spacenet_14")
        session.get(RecordingModel, recording_id).source_data_sha256 = "1" * 64
        session.commit()


def _probe_unavailable(client):
    probe = FakeProbe(available=False)
    client.app.state.remote_executor_probe = probe
    client.app.state.executor_registry = FakeRegistry(
        {"remote_gpu": FakeProvider("remote_gpu", probe=probe)}
    )


def _run_row_count(client, recording_id="rec_sn"):
    from app.analysis.model import AnalysisRunModel
    from sqlalchemy import select
    with client.app.state.database.session_factory() as session:
        return len(list(session.scalars(
            select(AnalysisRunModel).where(
                AnalysisRunModel.recording_id == recording_id,
                AnalysisRunModel.executor == "remote_gpu",
            )
        )))


def _create_remote(client, recording_id="rec_sn"):
    return client.post("/api/analysis-runs", json={
        "recording_id": recording_id,
        "pipeline_id": PIPELINE_ID,
        "executor": "remote_gpu",
        "parameters": {},
    })


def test_probe_unavailable_create_run_rejected_without_run(client):
    _add_sn_recording(client)
    _probe_unavailable(client)
    response = _create_remote(client)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert _run_row_count(client) == 0


def test_missing_mapping_returns_transport_unavailable(client, tmp_path):
    """A real SshRemoteExecutorProbe over a core-valid profile whose scientific
    mappings are empty fails its full-worker preflight -> availability false with
    REMOTE_TRANSPORT_UNAVAILABLE, HTTP healthy, and create_run rejected."""
    _add_sn_recording(client)
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    profile = RemoteProfile(
        name="autodl_primary",
        host="auto.example.com",
        port=22,
        user="root",
        ssh_key_path=key,
        known_hosts_path=hosts,
        remote_repo_root=PurePosixPath("/root/repo"),
        remote_job_root=PurePosixPath("/root/jobs"),
        remote_python_path=PurePosixPath("/opt/wsp-runtime/bin/python"),
        required_remote_runtime_commit=RUN,
        dataset_roots={},
    )
    recorder = ProcessRecorder()
    transport = SshRunner(profile, run_process=recorder)
    client.app.state.remote_executor_probe = SshRemoteExecutorProbe(
        profile, transport,
        expected_runtime_commit=RUN,
    )
    client.app.state.executor_registry = FakeRegistry(
        {"remote_gpu": FakeProvider("remote_gpu", probe=client.app.state.remote_executor_probe)}
    )

    response = client.get("/api/executor-availability",
                          params={"recording_id": "rec_sn", "pipeline_id": PIPELINE_ID})
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason_code"] == "REMOTE_TRANSPORT_UNAVAILABLE"
    assert recorder.calls == []  # preflight failed before any SSH invocation

    rejected = _create_remote(client)
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "REMOTE_TRANSPORT_UNAVAILABLE"
    assert _run_row_count(client) == 0


class ProcessRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# runtime-drift uncertain submit: reconcile SAME batch, never spawn under R2
# ---------------------------------------------------------------------------


def _batch_metadata(run_id="run_a", token="tok_a", runtime=RUN):
    from app.remote_execution.request_builder import freeze_request_provenance

    metadata = freeze_request_provenance(
        local_run_id=run_id,
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id=PIPELINE_ID,
        pipeline_version="1.0.0",
        required_remote_runtime_commit=runtime,
        orchestrator_commit=runtime,
        asset_manifest_sha256=MANIFEST,
        remote_profile="autodl_primary",
    )
    metadata["coordinator_token"] = token
    return metadata


class DriftSubmitJobManager:
    """submit always raises the drift REMOTE_SUBMIT_FAILED WITHOUT runner_code
    (the frozen batch keeps runtime R1 and is never re-spawned under R2)."""

    def __init__(self, status_sequence=None, status_error=None):
        self.submitted_batches = []
        self.status_batch_ids = []
        self.status_sequence = list(status_sequence or [])
        self.status_error = status_error

    def submit(self, batch, request_json_path):
        self.submitted_batches.append(batch)
        raise PlatformError(
            "REMOTE_SUBMIT_FAILED",
            "Frozen request runtime does not match the configured remote runtime.",
        )

    def status(self, batch_id):
        self.status_batch_ids.append(batch_id)
        if self.status_error is not None:
            raise self.status_error
        return self.status_sequence.pop(0)


def _seed_run(client, metadata, status="pending"):
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id=metadata["local_run_id"], recording_id="rec_sn",
            pipeline_id=PIPELINE_ID, pipeline_version="1.0.0",
            executor="remote_gpu", status=status, parameters_json={},
            execution_metadata_json=metadata,
        ))
        session.commit()


def _drift_coordinator(client, job_manager, metadata):
    return Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=job_manager,
        metadata=metadata,
        coordinator_token=metadata["coordinator_token"],
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        max_polls=1,
        ingest=lambda *a, **k: "unused",
        writer_factory=lambda session, run: SimpleNamespace(),
    )


def test_drift_reconciles_same_batch_and_continues_when_remote_job_exists(client):
    _add_sn_recording(client)
    metadata = _batch_metadata(run_id="run_a", token="tok_a", runtime=RUN)
    _seed_run(client, metadata)
    job_manager = DriftSubmitJobManager(status_sequence=[
        RemoteBatchStatusV1(batch_id=metadata["batch_id"], status="running",
                            items=[RemoteItemStatusV1(item_key="ik", status="running")]),
    ])

    result = _drift_coordinator(client, job_manager, metadata).run()

    assert result == "running"
    assert len(job_manager.submitted_batches) == 1
    # The frozen batch was presented to submit unchanged under runtime R1 and was
    # never re-frozen under a new runtime.
    assert job_manager.submitted_batches[0].required_remote_runtime_commit == RUN
    assert job_manager.submitted_batches[0].batch_id == metadata["batch_id"]
    # Reconciliation queried the SAME batch id.
    assert job_manager.status_batch_ids == [metadata["batch_id"]]
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_a")
        assert run.status == "running"


def test_drift_with_no_remote_job_fails_closed(client):
    _add_sn_recording(client)
    metadata = _batch_metadata(run_id="run_a", token="tok_a", runtime=RUN)
    _seed_run(client, metadata)
    # The remote runner reports the job is unrecoverable (controlled runner code).
    job_manager = DriftSubmitJobManager(
        status_error=PlatformError(
            "REMOTE_STATUS_UNAVAILABLE",
            "remote status unavailable",
            details={"runner_code": "REMOTE_JOB_INTERRUPTED"},
        )
    )

    result = _drift_coordinator(client, job_manager, metadata).run()

    assert result == "interrupted"
    assert len(job_manager.submitted_batches) == 1
    assert job_manager.submitted_batches[0].required_remote_runtime_commit == RUN
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_a")
        assert run.status == "interrupted"
        assert run.error_type == "REMOTE_JOB_INTERRUPTED"


def test_negative_deployment_does_not_corrupt_existing_completed_run(client):
    from benchmark_fixture import add_detection

    _add_sn_recording(client)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_old", recording_id="rec_sn", pipeline_id=PIPELINE_ID,
            pipeline_version="1.0.0", executor="remote_gpu", status="completed",
            parameters_json={},
        ))
        add_detection(session, detection_id="det_old", run_id="run_old",
                      class_id=9, class_name="LoRa 250kHz", confidence=0.94,
                      t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0)
        session.commit()
    _probe_unavailable(client)

    availability = client.get("/api/executor-availability",
                              params={"recording_id": "rec_sn", "pipeline_id": PIPELINE_ID})
    assert availability.status_code == 200
    assert availability.json()["available"] is False

    rejected = _create_remote(client)
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert _run_row_count(client) == 1  # only the pre-existing run remains

    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_old")
        assert run.status == "completed"
        rows = list(session.query(DetectionResultModel)
                    .filter(DetectionResultModel.run_id == "run_old").all())
        assert len(rows) == 1
        assert rows[0].id == "det_old"
        assert rows[0].confidence == 0.94