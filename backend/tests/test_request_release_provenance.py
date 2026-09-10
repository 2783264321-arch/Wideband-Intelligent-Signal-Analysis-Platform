"""TASK B3 — model release in request wire identity + result ingest."""

import pytest

from app.core.errors import PlatformError
from app.remote_execution.canonical import canonical_request_payload, compute_request_sha256
from app.remote_execution.request_builder import (
    FROZEN_REQUEST_KEYS,
    build_batch,
    freeze_request_provenance,
)
from app.remote_execution.result_ingestor import ingest_remote_result
from test_remote_result_ingestor import (
    _envelope,
    _seed_remote_run,
    _write_analysis_result_zip,
    _writer,
)

LEGACY_REQUEST_SHA256 = "a96504b07998779d9053cc0ca472da3e5746c6740a765aaf7d1dde29b04ecc6c"
MANIFEST_SHA = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"

_FREEZE_KWS = dict(
    local_run_id="run_fixture",
    recording_fingerprint="a" * 64,
    source_data_sha256="b" * 64,
    dataset_name="SpaceNet",
    dataset_split="test",
    dataset_key="0",
    label_space="spacenet_14",
    pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version="1.0.0",
    required_remote_runtime_commit="5bb5be4b04d04a071bc9d8f4f61172595ecee037",
    orchestrator_commit="d" * 40,
    asset_manifest_sha256=MANIFEST_SHA,
    remote_profile="remote",
)

# Task 0 legacy fixture: built with no model_release_id, id_factory -> "id_fixture".
LEGACY_FIXTURE_METADATA = {
    "request_id": "id_fixture",
    "batch_id": "id_fixture",
    "item_key": "id_fixture",
    "local_run_id": "run_fixture",
    "orchestrator_commit": "d" * 40,
    "required_remote_runtime_commit": "5bb5be4b04d04a071bc9d8f4f61172595ecee037",
    "asset_manifest_sha256": MANIFEST_SHA,
    "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3",
    "pipeline_version": "1.0.0",
    "remote_profile": "remote",
    "recording_fingerprint": "a" * 64,
    "source_data_sha256": "b" * 64,
    "dataset_name": "SpaceNet",
    "dataset_split": "test",
    "dataset_key": "0",
    "label_space": "spacenet_14",
    "parameters": {},
}


def _freeze(**overrides):
    return freeze_request_provenance(
        **_FREEZE_KWS,
        id_factory=lambda: "id_fixture",
        **overrides,
    )


def test_legacy_request_hash_byte_exact():
    batch = build_batch(LEGACY_FIXTURE_METADATA)
    assert batch.request_sha256 == LEGACY_REQUEST_SHA256
    assert compute_request_sha256(batch) == LEGACY_REQUEST_SHA256


def test_freeze_without_release_retains_legacy_sha():
    metadata = _freeze()
    assert metadata["model_release_id"] is None
    assert metadata["request_sha256"] == LEGACY_REQUEST_SHA256
    assert build_batch(metadata).request_sha256 == LEGACY_REQUEST_SHA256


def test_none_model_release_id_omitted_from_canonical_payload():
    batch = build_batch(LEGACY_FIXTURE_METADATA)
    assert "model_release_id" not in canonical_request_payload(batch)["pipeline"]


def test_two_release_ids_sharing_one_manifest_are_distinguishable():
    a = build_batch({**LEGACY_FIXTURE_METADATA, "model_release_id": "release_a"})
    b = build_batch({**LEGACY_FIXTURE_METADATA, "model_release_id": "release_b"})
    assert a.asset_manifest_sha256 == b.asset_manifest_sha256 == MANIFEST_SHA
    assert a.pipeline.model_release_id == "release_a"
    assert b.pipeline.model_release_id == "release_b"
    assert a.request_sha256 != b.request_sha256
    assert a.request_sha256 != LEGACY_REQUEST_SHA256
    assert b.request_sha256 != LEGACY_REQUEST_SHA256


def test_request_identification_roundtrip_with_release():
    metadata = _freeze(model_release_id="golden")
    assert metadata["model_release_id"] == "golden"
    assert "model_release_id" in FROZEN_REQUEST_KEYS
    batch = build_batch(metadata)
    assert canonical_request_payload(batch)["pipeline"]["model_release_id"] == "golden"
    assert compute_request_sha256(batch) == metadata["request_sha256"]
    assert batch.request_sha256 != LEGACY_REQUEST_SHA256


def _set_local_release(run, release_id):
    metadata = dict(run.execution_metadata_json)
    if release_id is None:
        metadata.pop("model_release_id", None)
    else:
        metadata["model_release_id"] = release_id
    run.execution_metadata_json = metadata


def test_ingest_allows_legacy_without_release(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running")
    _set_local_release(run, None)
    session.commit()
    writer = _writer(session, settings, workspace)

    assert ingest_remote_result(session, "run_r", _envelope(payload), zip_path, writer) == payload


def test_ingest_accepts_matching_release(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running")
    _set_local_release(run, "golden")
    session.commit()
    writer = _writer(session, settings, workspace)

    envelope = _envelope(payload, model_release_id="golden")
    assert ingest_remote_result(session, "run_r", envelope, zip_path, writer) == payload


def test_ingest_rejects_release_mismatch(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running")
    _set_local_release(run, "golden")
    session.commit()
    writer = _writer(session, settings, workspace)

    envelope = _envelope(payload, model_release_id="other_release")
    with pytest.raises(PlatformError) as excinfo:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert excinfo.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0
