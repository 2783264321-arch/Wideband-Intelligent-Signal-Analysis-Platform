import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.schema import (
    RemoteBatchStatusV1,
    RemoteItemStatusV1,
)

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
    item = RemoteItemStatusV1(item_key="ik", status=status)
    return RemoteBatchStatusV1(batch_id="bid_1", status=status, items=[item])


class FakeJobManager:
    def __init__(self, status_sequence=None):
        self.status_sequence = list(status_sequence or [])
        self.submitted = []
        self.downloaded = []

    def submit(self, batch, request_json_path):
        self.submitted.append((batch.batch_id, batch.request_sha256))
        return None

    def status(self, batch_id):
        if self.status_sequence:
            item = self.status_sequence.pop(0)
            return item if callable(item) is False else item(batch_id) if callable(item) else item
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status transport failed.")

    def download(self, batch_id, item_key, dest_dir):
        self.downloaded.append(item_key)
        dest = Path(dest_dir) / item_key
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "envelope.json").write_text(json.dumps({
            "schema_version": 1, "request_id": "req", "batch_id": batch_id,
            "item_key": item_key, "local_run_id": "run_x",
            "recording_fingerprint": "2" * 64, "source_data_sha256": "1" * 64,
            "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3", "pipeline_version": "1.0.0",
            "orchestrator_commit": RUN, "remote_runtime_commit": RUN,
            "asset_manifest_sha256": MANIFEST, "hardware": {"device": 0},
            "payload_sha256": "3" * 64,
        }))
        (dest / "analysis_result.zip").write_bytes(b"zip-bytes")
        return dest


class FakeIngestor:
    def __init__(self):
        self.calls = []

    def __call__(self, session, run_id, envelope, zip_path, writer):
        self.calls.append({"run_id": run_id, "zip": str(zip_path)})
        return envelope.payload_sha256


def _add_run(client, run_id="run_x", status="pending", token="tok_1"):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id="rec", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.add(AnalysisRunModel(
            id=run_id, recording_id="rec", pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
            pipeline_version="1.0.0", executor="remote_gpu", status=status,
            parameters_json={}, execution_metadata_json=_batch_metadata(run_id, token),
        ))
        session.commit()


def _sf(client):
    return client.app.state.database.session_factory


def _get_run(client, run_id="run_x"):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, run_id)
        return run.status, run.started_at, run.finished_at


def test_coordinator_rejects_stale_fencing_token_before_poll(client):
    _add_run(client, status="pending", token="tok_1")
    meta = _batch_metadata(token="tok_OTHER")
    jm = FakeJobManager(status_sequence=[_status("running")])
    coord = Coordinator(
        session_factory=_sf(client), job_manager=jm, metadata=meta, coordinator_token="tok_OTHER",
        sleep_fn=lambda *a, **k: None, poll_interval=0.01, ingest=FakeIngestor(),
    )
    terminal = coord.run()
    assert jm.submitted == []  # no submit/poll side effect
    assert _get_run(client)[0] == "pending"  # left untouched


def test_coordinator_queued_running_completed_sequence(client):
    _add_run(client, status="pending", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("queued"), _status("running"), _status("completed")])
    fake = FakeIngestor()
    coord = Coordinator(
        session_factory=_sf(client), job_manager=jm, metadata=meta, coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None, poll_interval=0.01, ingest=fake,
    )
    assert coord.run() == "completed"
    assert len(fake.calls) == 1
    status, started, finished = _get_run(client)
    assert status == "completed"
    assert started is not None and finished is not None


def test_coordinator_repeated_completion_is_idempotent(client):
    _add_run(client, status="completed", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("completed")])
    fake = FakeIngestor()
    coord = Coordinator(
        session_factory=_sf(client), job_manager=jm, metadata=meta, coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None, poll_interval=0.01, ingest=fake,
    )
    coord.run()
    assert fake.calls == []  # already completed -> no re-ingest


def test_coordinator_failed_marks_failed(client):
    _add_run(client, status="running", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("failed")])
    coord = Coordinator(
        session_factory=_sf(client), job_manager=jm, metadata=meta, coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None, poll_interval=0.01, ingest=FakeIngestor(),
    )
    assert coord.run() == "failed"
    assert _get_run(client)[0] == "failed"


def test_coordinator_interrupted_marks_interrupted(client):
    _add_run(client, status="running", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("interrupted")])
    coord = Coordinator(
        session_factory=_sf(client), job_manager=jm, metadata=meta, coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None, poll_interval=0.01, ingest=FakeIngestor(),
    )
    assert coord.run() == "interrupted"
    assert _get_run(client)[0] == "interrupted"


def test_coordinator_request_sha_reconstructs_from_persisted_metadata():
    meta = _batch_metadata(token="tok_1")
    batch = build_batch(meta)
    assert batch.request_sha256 == meta["request_sha256"]


def test_coordinator_never_invokes_local_worker():
    from app.remote_execution.coordinator_job_manager import CoordinatorJobManager
    assert CoordinatorJobManager.DEFAULT_COORDINATOR_MODULE == "app.remote_execution.coordinator"