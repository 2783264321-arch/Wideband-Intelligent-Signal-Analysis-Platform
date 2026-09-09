import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator
from app.remote_execution.request_builder import freeze_request_provenance
from app.remote_execution.schema import RemoteBatchStatusV1, RemoteItemStatusV1

RUN = "a" * 40
MANIFEST = "b" * 64


def _batch_metadata(run_id="run_x", token="tok_1"):
    m = freeze_request_provenance(
        local_run_id=run_id,
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version="1.0.0",
        required_remote_runtime_commit=RUN,
        orchestrator_commit=RUN,
        asset_manifest_sha256=MANIFEST,
        remote_profile="autodl_primary",
    )
    m["coordinator_token"] = token
    return m


def _status(status):
    return RemoteBatchStatusV1(batch_id="bid_1", status=status,
                               items=[RemoteItemStatusV1(item_key="ik", status=status)])


class SubmitFailureJobManager:
    def __init__(self, submit_exc=None, status_sequence=None):
        self.submit_exc = submit_exc
        self.status_sequence = list(status_sequence or [])
        self.submitted = 0
        self.status_calls = 0

    def submit(self, batch, request_json_path):
        self.submitted += 1
        if self.submit_exc is not None:
            raise self.submit_exc
        return None

    def status(self, batch_id):
        self.status_calls += 1
        if self.status_sequence:
            item = self.status_sequence.pop(0)
            return item if not callable(item) else item(batch_id)
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status transport failed.")

    def download(self, batch_id, item_key, dest_dir):
        raise AssertionError("download should not be reached in these tests")


def _add_run(client):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id="rec", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.add(AnalysisRunModel(
            id="run_x", recording_id="rec", pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
            pipeline_version="1.0.0", executor="remote_gpu", status="pending",
            parameters_json={}, execution_metadata_json=_batch_metadata(),
        ))
        session.commit()


def _get_run(client):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        return session.get(AnalysisRunModel, "run_x")


def _coord(client, jm):
    return Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=jm,
        metadata=_batch_metadata(),
        coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        ingest=lambda session, run, env, zp, w: env.payload_sha256,
        writer_factory=lambda session, run: SimpleNamespace(session=session),
        max_polls=5,
    )


def test_controlled_remote_submit_invalid_marks_interrupted(client):
    _add_run(client)
    jm = SubmitFailureJobManager(
        submit_exc=PlatformError("REMOTE_SUBMIT_FAILED", "submit rejected",
                                 details={"runner_code": "REMOTE_SUBMIT_INVALID"}),
    )
    coord = _coord(client, jm)
    assert coord.run() == "interrupted"
    run = _get_run(client)
    assert run.status == "interrupted"
    assert run.error_type == "REMOTE_SUBMIT_INVALID"
    assert run.finished_at is not None
    assert jm.status_calls == 0  # no infinite status loop


def test_controlled_submit_runner_code_marks_interrupted(client):
    _add_run(client)
    jm = SubmitFailureJobManager(
        submit_exc=PlatformError("REMOTE_SUBMIT_FAILED", "rejected",
                                 details={"runner_code": "REMOTE_IMPLEMENTATION_MISMATCH"}),
    )
    coord = _coord(client, jm)
    assert coord.run() == "interrupted"
    run = _get_run(client)
    assert run.status == "interrupted"
    assert run.error_type == "REMOTE_IMPLEMENTATION_MISMATCH"
    assert jm.status_calls == 0


def test_generic_submit_failure_reconciles_same_batch(client):
    _add_run(client)
    jm = SubmitFailureJobManager(
        submit_exc=PlatformError("REMOTE_SUBMIT_FAILED", "transport failed"),
        status_sequence=[_status("running")],
    )
    coord = _coord(client, jm)
    coord.run()
    # No runner_code -> uncertain submit: reconcile same batch (submit attempted again
    # on next loop iteration), NOT terminal interrupted, and status IS queried.
    assert jm.submitted >= 1
    assert jm.status_calls >= 1
    assert _get_run(client).status in {"pending", "running"}


def test_controlled_unrecoverable_status_runner_code_marks_interrupted(client):
    _add_run(client)
    jm = SubmitFailureJobManager()
    jm.status_sequence = [
        lambda b: (_ for _ in ()).throw(
            PlatformError("REMOTE_STATUS_UNAVAILABLE", "job missing",
                          details={"runner_code": "REMOTE_JOB_INTERRUPTED"}))
    ]
    coord = _coord(client, jm)
    assert coord.run() == "interrupted"
    run = _get_run(client)
    assert run.status == "interrupted"
    assert run.error_type == "REMOTE_JOB_INTERRUPTED"
    assert jm.status_calls == 1  # not an infinite retry


def test_generic_status_failure_remains_recoverable(client):
    _add_run(client)
    jm = SubmitFailureJobManager()
    jm.status_sequence = [
        lambda b: (_ for _ in ()).throw(PlatformError("REMOTE_STATUS_UNAVAILABLE", "transient")),
        _status("running"),
    ]
    coord = _coord(client, jm)
    coord.run()
    assert jm.status_calls >= 2  # retried, then recovered
    assert _get_run(client).status == "running"