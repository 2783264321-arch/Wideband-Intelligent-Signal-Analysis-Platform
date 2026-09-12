from app.analysis.model import AnalysisRunModel
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.request_builder import build_batch

_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}

# Complete minimal legacy fixture defined locally (do not import another test
# module). Mirrors the frozen Task-B3 fixture with deterministic ids.
_LEGACY_FIXTURE_METADATA = {
    "request_id": "id_fixture",
    "batch_id": "id_fixture",
    "item_key": "id_fixture",
    "local_run_id": "run_fixture",
    "orchestrator_commit": "d" * 40,
    "required_remote_runtime_commit": "5bb5be4b04d04a071bc9d8f4f61172595ecee037",
    "asset_manifest_sha256": "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08",
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
_LEGACY_REQUEST_SHA256 = "a96504b07998779d9053cc0ca472da3e5746c6740a765aaf7d1dde29b04ecc6c"


def test_analysis_run_schema_unchanged_by_g2():
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert not any("experiment" in name for name in AnalysisRunModel.__table__.columns.keys())
    assert "launch_requested_at" not in AnalysisRunModel.__table__.columns.keys()


def test_legacy_request_hash_byte_exact_after_g2():
    batch = build_batch(_LEGACY_FIXTURE_METADATA)
    assert batch.request_sha256 == _LEGACY_REQUEST_SHA256
    assert compute_request_sha256(batch) == _LEGACY_REQUEST_SHA256
