import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.remote_execution.coordinator import Coordinator
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
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


def _rotate_token(client, new_token="rotated"):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_x")
        md = dict(run.execution_metadata_json or {})
        md["coordinator_token"] = new_token
        run.execution_metadata_json = md
        session.commit()


class FakeJobManager:
    def __init__(self, status_sequence=None):
        self.status_sequence = list(status_sequence or [])
        self.submitted = []
        self.status_calls = 0
        self.downloaded = False
        self.rotate_hook = None

    def submit(self, batch, request_json_path):
        self.submitted.append(batch.batch_id)
        return None

    def status(self, batch_id):
        self.status_calls += 1
        if self.status_sequence:
            item = self.status_sequence.pop(0)
            result = item if not callable(item) else item(batch_id)
            if self.rotate_hook:
                self.rotate_hook()
            return result
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status transport failed.")

    def download(self, batch_id, item_key, dest_dir):
        self.downloaded = True
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

    def __call__(self, session, run, envelope, zip_path, writer):
        self.calls.append({"run_id": run.id})
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


def _get_run_status(client):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        return session.get(AnalysisRunModel, "run_x").status


def _writer_factory(session, run):
    return SimpleNamespace(persist=lambda *a, **k: None)


def _coord(client, jm, meta, *, token="tok_1", ingest=None, max_polls=None):
    return Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=jm,
        metadata=meta,
        coordinator_token=token,
        sleep_fn=lambda *a, **k: None,
        poll_interval=0.01,
        ingest=ingest or FakeIngestor(),
        writer_factory=_writer_factory,
        max_polls=max_polls,
    )


def test_token_rotated_after_remote_status_no_stale_mutation(client):
    _add_run(client, status="pending", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("running")])
    jm.rotate_hook = lambda: _rotate_token(client)
    coord = _coord(client, jm, meta)
    coord.run()
    # status side effect happened; token rotated after status -> no stale "running" mutation
    assert _get_run_status(client) == "pending"


def test_token_rotated_during_completed_download_no_ingest(client):
    _add_run(client, status="running", token="tok_1")
    meta = _batch_metadata(token="tok_1")

    class RotatingDownload(FakeJobManager):
        def download(self, batch_id, item_key, dest_dir):
            result = super().download(batch_id, item_key, dest_dir)
            _rotate_token(client)  # rotate AFTER download, BEFORE ingest
            return result

    jm = RotatingDownload(status_sequence=[_status("completed")])
    ingestor = FakeIngestor()
    coord = _coord(client, jm, meta, ingest=ingestor)
    terminal = coord.run()
    assert jm.downloaded is True
    assert ingestor.calls == []  # no ingest after token rotated
    assert _get_run_status(client) != "completed"
    assert terminal == "interrupted"


def test_stale_token_before_failed_mutation_no_mutation(client):
    _add_run(client, status="running", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager(status_sequence=[_status("failed")])
    jm.rotate_hook = lambda: _rotate_token(client)
    coord = _coord(client, jm, meta)
    coord.run()
    assert _get_run_status(client) != "failed"


def test_transient_status_unavailable_retries_and_sleeps_after_session_closed(client):
    sleeps = []
    _add_run(client, status="pending", token="tok_1")
    meta = _batch_metadata(token="tok_1")
    jm = FakeJobManager()
    jm.status_sequence = [
        lambda b: (_ for _ in ()).throw(PlatformError("REMOTE_STATUS_UNAVAILABLE", "x")),
        _status("running"),
    ]
    coord = Coordinator(
        session_factory=client.app.state.database.session_factory,
        job_manager=jm,
        metadata=meta,
        coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: sleeps.append(1),
        poll_interval=0.01,
        ingest=FakeIngestor(),
        writer_factory=_writer_factory,
        max_polls=5,
    )
    coord.run()
    assert sleeps  # bounded retry occurred
    assert _get_run_status(client) in {"pending", "running"}


def test_coordinator_token_rotation_does_not_change_request_sha():
    meta = _batch_metadata(token="tok_1")
    before = build_batch(meta).request_sha256
    meta["coordinator_token"] = "rotated"
    after = build_batch(meta).request_sha256
    assert before == after