"""Public execution-metadata boundary (Task 12F-C corrective).

The API read model must expose ONLY the public execution-metadata allowlist:
remote_profile, required_remote_runtime_commit, remote_runtime_commit,
payload_sha256, remote_started_at, remote_finished_at. Internal fields
(coordinator_token, request_id, batch_id, item_key, request_sha256,
recording_fingerprint, source_data_sha256, orchestrator_commit) must never be
serialized to API/browser clients. The DB persists the full internal metadata
unchanged (coordinator/recovery still need it).
"""
from __future__ import annotations

from benchmark_fixture import add_recording

from app.analysis.model import AnalysisRunModel
from app.remote_execution.request_builder import freeze_request_provenance
from app.recordings.model import RecordingModel

RUN = "a" * 40
MANIFEST = "b" * 64
RECORDING_ID = "rec_pub"


def _internal_metadata(run_id="run_x", token="tok_x"):
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
    m["remote_runtime_commit"] = RUN
    m["payload_sha256"] = "c" * 64
    m["remote_started_at"] = "2026-09-09T15:28:41.632869+00:00"
    m["remote_finished_at"] = "2026-09-09T15:28:54.924629+00:00"
    return m


def _seed(client, metadata):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=RECORDING_ID, name="0",
                      dataset_name="SpaceNet", dataset_split="test",
                      label_space="spacenet_14")
        session.get(RecordingModel, RECORDING_ID).source_data_sha256 = "1" * 64
        session.add(AnalysisRunModel(
            id="run_pub", recording_id=RECORDING_ID,
            pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
            pipeline_version="1.0.0", executor="remote_gpu", status="completed",
            parameters_json={}, execution_metadata_json=metadata,
        ))
        session.commit()


PUBLIC_ALLOWLIST = {
    "remote_profile",
    "required_remote_runtime_commit",
    "remote_runtime_commit",
    "payload_sha256",
    "remote_started_at",
    "remote_finished_at",
}

INTERNAL_FORBIDDEN = {
    "coordinator_token",
    "request_id",
    "batch_id",
    "item_key",
    "request_sha256",
    "recording_fingerprint",
    "source_data_sha256",
    "orchestrator_commit",
}


def test_api_read_exposes_public_metadata_allowlist_only(client):
    metadata = _internal_metadata()
    _seed(client, metadata)

    response = client.get("/api/analysis-runs/run_pub")
    assert response.status_code == 200
    body = response.json()
    meta = body["execution_metadata_json"] or {}
    assert set(meta.keys()) <= PUBLIC_ALLOWLIST
    for field in INTERNAL_FORBIDDEN:
        assert field not in meta, f"internal field leaked: {field}"
    assert meta["remote_profile"] == "autodl_primary"
    assert meta["required_remote_runtime_commit"] == RUN
    assert meta["payload_sha256"] == "c" * 64
    assert "coordinator_token" not in str(body)


def test_db_internal_metadata_preserved_for_coordinator(client):
    metadata = _internal_metadata()
    _seed(client, metadata)

    client.get("/api/analysis-runs/run_pub")  # read must not mutate persistence

    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_pub")
        stored = dict(run.execution_metadata_json or {})
        assert stored["coordinator_token"] == "tok_x"
        assert stored["request_id"]
        assert stored["batch_id"]
        assert stored["recording_fingerprint"] == "2" * 64
        assert stored["source_data_sha256"] == "1" * 64
        assert stored["orchestrator_commit"] == RUN