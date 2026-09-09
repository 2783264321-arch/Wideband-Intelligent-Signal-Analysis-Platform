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


class CountingSession:
    """Wraps a real DB session, tracking the active-session counter."""

    def __init__(self, real_session, counter):
        self._real_session = real_session
        self._counter = counter

    def __enter__(self):
        self._real_session.__enter__()
        self._counter["active"] += 1
        return self._real_session

    def __exit__(self, *a):
        self._counter["active"] -= 1
        return self._real_session.__exit__(*a)


class InstrumentedJobManager:
    def __init__(self, client, *, status_sequence, counter, extra_checks):
        self.client = client
        self.status_sequence = list(status_sequence)
        self.counter = counter
        self.extra_checks = extra_checks
        self.submitted = []

    def submit(self, batch, request_json_path):
        self.submitted.append(batch.batch_id)
        return None

    def status(self, batch_id):
        assert self.counter["active"] == 0, "status() must run with zero DB sessions open"
        if self.status_sequence:
            item = self.status_sequence.pop(0)
            return item if not callable(item) else item(batch_id)
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "x")

    def download(self, batch_id, item_key, dest_dir):
        assert self.counter["active"] == 0, "download() must run with zero DB sessions open"
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
    def __init__(self, counter):
        self.counter = counter
        self.calls = []

    def __call__(self, session, run, envelope, zip_path, writer):
        # Exactly one final session must be active during ingest.
        assert self.counter["active"] == 1, "ingest must run with exactly one active session"
        self.calls.append({"session": session, "writer": writer})
        return envelope.payload_sha256


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


def _counting_session_factory(client, counter):
    def factory():
        return CountingSession(client.app.state.database.session_factory(), counter)
    return factory


def test_completed_path_download_and_sleep_have_zero_sessions_open(client):
    _add_run(client)
    counter = {"active": 0}
    sleeps = []
    jm = InstrumentedJobManager(
        client,
        status_sequence=[_status("queued"), _status("running"), _status("completed")],
        counter=counter,
        extra_checks={},
    )
    ingestor = RecordingIngestor(counter)
    writer_sessions = []

    def writer_factory(session, run):
        # Exactly one final session active during writer construction.
        assert counter["active"] == 1
        writer_sessions.append(session)
        return SimpleNamespace(session=session)

    coord = Coordinator(
        session_factory=_counting_session_factory(client, counter),
        job_manager=jm,
        metadata=_batch_metadata(),
        coordinator_token="tok_1",
        sleep_fn=lambda *a, **k: assert_zero(counter),
        poll_interval=0.01,
        ingest=ingestor,
        writer_factory=writer_factory,
    )
    assert coord.run() == "completed"
    assert counter["active"] == 0  # all sessions closed at end
    assert ingestor.calls
    # writer.session is the same final session used for ingest.
    assert ingestor.calls[0]["writer"].session is ingestor.calls[0]["session"]
    assert writer_sessions[0] is ingestor.calls[0]["session"]


def assert_zero(counter):
    assert counter["active"] == 0, "sleep must run with zero DB sessions open"


def test_transient_status_sleep_has_zero_sessions_open(client):
    _add_run(client)
    counter = {"active": 0}
    sleeps = []
    jm = InstrumentedJobManager(client, status_sequence=[], counter=counter, extra_checks={})
    jm.status_sequence = [
        lambda b: (_ for _ in ()).throw(PlatformError("REMOTE_STATUS_UNAVAILABLE", "x")),
        _status("running"),
    ]

    def sleep_fn(interval):
        assert counter["active"] == 0
        sleeps.append(interval)

    coord = Coordinator(
        session_factory=_counting_session_factory(client, counter),
        job_manager=jm,
        metadata=_batch_metadata(),
        coordinator_token="tok_1",
        sleep_fn=sleep_fn,
        poll_interval=0.01,
        ingest=RecordingIngestor(counter),
        writer_factory=lambda session, run: SimpleNamespace(session=session),
        max_polls=5,
    )
    coord.run()
    assert counter["active"] == 0
    assert sleeps