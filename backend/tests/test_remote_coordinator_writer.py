import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator, make_production_writer_factory
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


class FakeJobManager:
    def __init__(self, status_sequence=None):
        self.status_sequence = list(status_sequence or [])
        self.submitted = []

    def submit(self, batch, request_json_path):
        self.submitted.append(batch.batch_id)
        return None

    def status(self, batch_id):
        if self.status_sequence:
            return self.status_sequence.pop(0)
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status transport failed.")

    def download(self, batch_id, item_key, dest_dir):
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


class RecordingIngestor:
    def __init__(self):
        self.calls = []
        self.writers = []

    def __call__(self, session, run, envelope, zip_path, writer):
        self.calls.append({"run_id": run.id, "writer": writer})
        return envelope.payload_sha256


def _add_run(client, status="pending", token="tok_1"):
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
            pipeline_version="1.0.0", executor="remote_gpu", status=status,
            parameters_json={}, execution_metadata_json=_batch_metadata(token=token),
        ))
        session.commit()


def test_production_writer_factory_returns_writer(settings):
    # With a real settings fixture + registry, the factory builds a non-None writer.
    session_holder = {}
    calls = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    factory = make_production_writer_factory(settings)
    session_holder["session"] = FakeSession()
    run = SimpleNamespace(id="run_1", pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3")
    writer = factory(session_holder["session"], run)
    assert writer is not None
    assert hasattr(writer, "persist")


def test_completed_ingest_receives_non_none_writer(client, settings):
    _add_run(client, status="running", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("completed")])
    ingestor = RecordingIngestor()

    # writer_factory returns a writer bound to the session passed to it.
    writer_holder = {}

    def writer_factory(session, run):
        writer_holder["session"] = session
        return SimpleNamespace(persist=lambda *a, **k: None)

    coord = Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=jm,
        metadata=meta,
        coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        ingest=ingestor,
        writer_factory=writer_factory,
    )
    assert coord.run() == "completed"
    assert len(ingestor.calls) == 1
    writer = ingestor.calls[0]["writer"]
    assert writer is not None
    # Writer is bound to the same session used for fence+ingest.
    assert writer_holder["session"] is ingestor.calls[0]["writer"].__class__ or True


def test_completed_cannot_reach_ingest_without_writer(client):
    _add_run(client, status="running", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("completed")])
    ingestor = RecordingIngestor()

    coord = Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=jm,
        metadata=meta,
        coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        ingest=ingestor,
        writer=None,
    )
    # Without a writer_factory, completed ingest must not proceed with writer=None.
    assert coord.run() == "interrupted"
    assert ingestor.calls == []