"""M9.1-B Task 8 detached server remote runner tests.

These tests use only ``tmp_path`` + a fake ``ItemExecutor``. They never import
resolver/assets/ZoomSpec/torch/ultralytics and never touch GPU/network/DB.
The job directory contract is:

    job_root/
        request.json
        status.json
        results/
            <item_key>/
                envelope.json
                analysis_result.zip

The test helper does NOT pre-create ``job_root``; ``create_or_attach`` owns its
creation (the helper only pre-creates ``job_root.parent``).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from app.core.errors import PlatformError
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.result_ingestor import parse_remote_execution_envelope_json
from app.remote_execution.runner import (
    create_or_attach,
    reconcile_status,
    run_work,
    submit_job,
    validate_request_sha256,
)
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionEnvelopeV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.source_hash import compute_file_sha256

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"


def _make_batch(
    batch_id="batch_x",
    item_key="000000",
    *,
    parameters=None,
    request_id="req_1",
    local_run_id="run_x",
    runtime_commit=RUNTIME_COMMIT,
):
    """Build a batch with a VALID canonical request_sha256."""
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=runtime_commit,
        pipeline={"id": "pipeline_x", "version": "1.0"},
        asset_manifest_sha256="c" * 64,
        items=[
            RemoteExecutionItemV1(
                item_key=item_key,
                request_id=request_id,
                local_run_id=local_run_id,
                orchestrator_commit=ORCHESTRATOR_COMMIT,
                recording=RemoteRecordingRefV1(
                    dataset_name="SpaceNet",
                    dataset_split="test",
                    dataset_key="0",
                    label_space="spacenet_14",
                    expected_recording_fingerprint="a" * 64,
                    expected_source_data_sha256="b" * 64,
                ),
                parameters=parameters or {},
            ),
        ],
        request_sha256="0" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _make_batch_two_items():
    batch = _make_batch(item_key="000000", request_id="req_1", local_run_id="run_x")
    batch.items.append(
        RemoteExecutionItemV1(
            item_key="000001",
            request_id="req_2",
            local_run_id="run_y",
            orchestrator_commit=ORCHESTRATOR_COMMIT,
            recording=RemoteRecordingRefV1(
                dataset_name="SpaceNet",
                dataset_split="test",
                dataset_key="1",
                label_space="spacenet_14",
                expected_recording_fingerprint="a" * 64,
                expected_source_data_sha256="b" * 64,
            ),
            parameters={},
        )
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _jobs_parent(tmp_path: Path) -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs


def _job_root(tmp_path: Path, batch_id="batch_x") -> Path:
    # Only the PARENT is pre-created; create_or_attach owns job_root creation.
    return _jobs_parent(tmp_path) / batch_id


class FakeItemExecutor:
    """Publishes a REAL valid envelope + binary zip payload."""

    def __init__(self):
        self.execution_count = 0
        self.payload = b"synthetic-analysis-result"
        self.fail_item_keys: set[str] = set()

    def execute(self, item: RemoteExecutionItemV1, job_root: Path) -> None:
        self.execution_count += 1
        if item.item_key in self.fail_item_keys:
            raise PlatformError("PIPELINE_EXECUTION_FAILED", "synthetic failure")
        self._publish(item, job_root)

    def _publish(self, item: RemoteExecutionItemV1, job_root: Path) -> None:
        result_dir = job_root / "results" / item.item_key
        result_dir.mkdir(parents=True, exist_ok=True)
        envelope = RemoteExecutionEnvelopeV1(
            schema_version=1,
            request_id=item.request_id,
            batch_id=job_root.name,
            item_key=item.item_key,
            local_run_id=item.local_run_id,
            recording_fingerprint=item.recording.expected_recording_fingerprint,
            source_data_sha256=item.recording.expected_source_data_sha256,
            pipeline_id="pipeline_x",
            pipeline_version="1.0",
            orchestrator_commit=item.orchestrator_commit,
            remote_runtime_commit=RUNTIME_COMMIT,
            asset_manifest_sha256="c" * 64,
            hardware={"device": "fake"},
            payload_sha256=hashlib.sha256(self.payload).hexdigest(),
            remote_started_at=None,
            remote_finished_at=None,
        )
        # Atomic publication in the test fake.
        _atomic_write_bytes(result_dir / "envelope.json", envelope.model_dump_json().encode("utf-8"))
        _atomic_write_bytes(result_dir / "analysis_result.zip", self.payload)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def _status(batch_id: str, job_root: Path) -> dict:
    return json.loads((job_root / "status.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A. module missing / runner interfaces RED
# ---------------------------------------------------------------------------


def test_runner_module_imports_without_future_dependencies():
    import importlib
    import sys

    before = set(sys.modules)
    module = importlib.import_module("app.remote_execution.runner")
    assert hasattr(module, "ItemExecutor")
    assert hasattr(module, "validate_request_sha256")
    assert hasattr(module, "create_or_attach")
    assert hasattr(module, "reconcile_status")
    assert hasattr(module, "submit_job")
    assert hasattr(module, "run_work")
    # No future-module imports at module import time. resolver/assets are now
    # legitimate platform modules (Task 9), so assert runner's OWN import chain
    # does not newly load them rather than requiring global absence.
    newly_loaded = set(sys.modules) - before
    future = {
        "app.remote_execution.resolver",
        "app.remote_execution.assets",
        "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3",
        "torch",
        "ultralytics",
    }
    assert future.isdisjoint(newly_loaded)


# ---------------------------------------------------------------------------
# B. valid request_sha256 accepted
# ---------------------------------------------------------------------------


def test_valid_request_sha256_accepted(tmp_path):
    batch = _make_batch()
    validate_request_sha256(batch)


# ---------------------------------------------------------------------------
# C. invalid supplied request_sha256 -> REMOTE_REQUEST_INVALID
# ---------------------------------------------------------------------------


def test_invalid_supplied_request_sha256_rejected():
    batch = _make_batch()
    batch.request_sha256 = "f" * 64
    with pytest.raises(PlatformError) as exc:
        validate_request_sha256(batch)
    assert exc.value.code == "REMOTE_REQUEST_INVALID"


# ---------------------------------------------------------------------------
# D/E. create-or-attach idempotency + spawn count
# ---------------------------------------------------------------------------


def test_first_submit_creates_and_spawns_once(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    spawned = []

    def spawn(worker_batch_id: str) -> None:
        spawned.append(worker_batch_id)

    assert submit_job(batch, job_root, spawn) == "created"
    assert len(spawned) == 1
    assert spawned == [batch.batch_id]
    assert (job_root / "request.json").is_file()
    assert (job_root / "status.json").is_file()


def test_second_same_request_attaches_without_respawn(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    spawned = []

    def spawn(worker_batch_id: str) -> None:
        spawned.append(worker_batch_id)

    assert submit_job(batch, job_root, spawn) == "created"
    assert submit_job(batch, job_root, spawn) == "attached"
    assert len(spawned) == 1


# ---------------------------------------------------------------------------
# F. same batch id + different valid semantic request -> REMOTE_REQUEST_CONFLICT
# ---------------------------------------------------------------------------


def test_same_batch_id_different_request_conflicts(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    submit_job(batch, job_root, lambda _bid: None)

    changed = _make_batch()
    changed.items[0].parameters = {"x": 1}
    changed.request_sha256 = compute_request_sha256(changed)

    spawned = []

    def spawn(worker_batch_id: str) -> None:
        spawned.append(worker_batch_id)

    with pytest.raises(PlatformError) as exc:
        submit_job(changed, job_root, spawn)
    assert exc.value.code == "REMOTE_REQUEST_CONFLICT"
    assert len(spawned) == 0
    # Frozen request.json is NOT overwritten.
    stored = json.loads((job_root / "request.json").read_text(encoding="utf-8"))
    assert stored["request_sha256"] == batch.request_sha256


def test_invalid_hash_submit_spawns_nothing(tmp_path):
    batch = _make_batch()
    batch.request_sha256 = "f" * 64
    job_root = _job_root(tmp_path)
    spawned = []
    with pytest.raises(PlatformError) as exc:
        submit_job(batch, job_root, lambda bid: spawned.append(bid))
    assert exc.value.code == "REMOTE_REQUEST_INVALID"
    assert len(spawned) == 0


# ---------------------------------------------------------------------------
# G. stored malformed/incomplete request -> fail closed, no overwrite
# ---------------------------------------------------------------------------


def test_create_or_attach_fails_closed_on_corrupt_existing_request(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root / "request.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        create_or_attach(batch, job_root)
    assert exc.value.code == "REMOTE_JOB_INTERRUPTED"
    # The corrupt frozen request is not overwritten.
    assert (job_root / "request.json").read_text(encoding="utf-8") == "{ not valid json"


def test_create_or_attach_fails_closed_on_duplicate_key_request(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    job_root.mkdir(parents=True, exist_ok=True)
    text = '{"schema_version":1,"batch_id":"batch_x","batch_id":"batch_x"}'
    (job_root / "request.json").write_text(text, encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        create_or_attach(batch, job_root)
    assert exc.value.code == "REMOTE_JOB_INTERRUPTED"


def test_create_or_attach_fails_closed_on_stored_request_invalid_hash(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    job_root.mkdir(parents=True, exist_ok=True)
    stored = batch.model_dump()
    stored["request_sha256"] = "f" * 64
    (job_root / "request.json").write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        create_or_attach(batch, job_root)
    assert exc.value.code == "REMOTE_JOB_INTERRUPTED"


# ---------------------------------------------------------------------------
# H. initial status: queued batch, all queued items, exact membership
# ---------------------------------------------------------------------------


def test_initial_status_is_queued_with_exact_membership(tmp_path):
    batch = _make_batch_two_items()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    status = _status(batch.batch_id, job_root)
    assert status["batch_id"] == batch.batch_id
    assert status["status"] == "queued"
    item_keys = [item["item_key"] for item in status["items"]]
    assert item_keys == ["000000", "000001"]
    assert all(item["status"] == "queued" for item in status["items"])


# ---------------------------------------------------------------------------
# I/J. fresh run_work + write-once on second run_work
# ---------------------------------------------------------------------------


def test_fresh_run_work_completes_item_and_batch(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 1
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "completed"
    assert status.items[0].status == "completed"
    assert status.items[0].result_relative_path == "results/000000"
    result_dir = job_root / "results" / "000000"
    assert (result_dir / "envelope.json").is_file()
    assert (result_dir / "analysis_result.zip").is_file()


def test_second_run_work_is_write_once(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    envelope_first = (job_root / "results" / "000000" / "envelope.json").read_bytes()
    payload_first = (job_root / "results" / "000000" / "analysis_result.zip").read_bytes()
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 1
    assert (job_root / "results" / "000000" / "analysis_result.zip").read_bytes() == payload_first
    assert (job_root / "results" / "000000" / "envelope.json").read_bytes() == envelope_first


# ---------------------------------------------------------------------------
# K/L. completed status + corrupted artifact -> REMOTE_RESULT_CORRUPTED, no regen
# ---------------------------------------------------------------------------


def test_completed_status_missing_payload_is_interrupted_and_not_regenerated(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    payload_path = job_root / "results" / "000000" / "analysis_result.zip"
    payload_path.unlink()
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 1
    assert not payload_path.exists()
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "interrupted"
    assert status.items[0].status == "interrupted"
    assert status.items[0].error_code == "REMOTE_RESULT_CORRUPTED"


def test_completed_status_payload_hash_mismatch_is_interrupted(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    payload_path = job_root / "results" / "000000" / "analysis_result.zip"
    payload_path.write_bytes(b"tampered")
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 1
    assert payload_path.read_bytes() == b"tampered"  # never regenerated
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "interrupted"
    assert status.items[0].status == "interrupted"
    assert status.items[0].error_code == "REMOTE_RESULT_CORRUPTED"


def test_completed_status_missing_envelope_is_interrupted(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    (job_root / "results" / "000000" / "envelope.json").unlink()
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 1
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "interrupted"
    assert status.items[0].status == "interrupted"
    assert status.items[0].error_code == "REMOTE_RESULT_CORRUPTED"


# ---------------------------------------------------------------------------
# M. crash window A: running + both valid result files already exist -> reconcile
# ---------------------------------------------------------------------------


def test_running_with_valid_existing_result_reconciles_without_executor(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    # Simulate a crash: terminal files exist but status is still running.
    executor = FakeItemExecutor()
    executor._publish(batch.items[0], job_root)
    _write_status(batch.batch_id, job_root, [
        {"item_key": "000000", "status": "running"},
    ])
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 0
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "completed"
    assert status.items[0].status == "completed"


# ---------------------------------------------------------------------------
# N. crash window B: running + only one terminal file -> REMOTE_RESULT_CORRUPTED
# ---------------------------------------------------------------------------


def test_running_with_partial_result_is_interrupted_without_executor(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    result_dir = job_root / "results" / "000000"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "envelope.json").write_bytes(b"{}")
    _write_status(batch.batch_id, job_root, [
        {"item_key": "000000", "status": "running"},
    ])
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 0
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "interrupted"
    assert status.items[0].status == "interrupted"
    assert status.items[0].error_code == "REMOTE_RESULT_CORRUPTED"


# ---------------------------------------------------------------------------
# O. 2-item partial failure: first fails, second completes
# ---------------------------------------------------------------------------


def test_partial_item_failure_does_not_block_later_items(tmp_path):
    batch = _make_batch_two_items()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    executor.fail_item_keys = {"000000"}
    run_work(batch.batch_id, job_root, executor)
    status = reconcile_status(batch.batch_id, job_root)
    assert executor.execution_count == 2
    by_key = {item.item_key: item for item in status.items}
    assert by_key["000000"].status == "failed"
    assert by_key["000000"].error_code == "PIPELINE_EXECUTION_FAILED"
    assert by_key["000001"].status == "completed"
    assert status.status == "failed"  # any failed item -> batch failed


# ---------------------------------------------------------------------------
# P. failed item is not retried on second run_work
# ---------------------------------------------------------------------------


def test_failed_item_is_not_retried(tmp_path):
    batch = _make_batch_two_items()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    executor.fail_item_keys = {"000000"}
    run_work(batch.batch_id, job_root, executor)
    first_count = executor.execution_count
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == first_count
    status = reconcile_status(batch.batch_id, job_root)
    assert status.items[0].status == "failed"


# ---------------------------------------------------------------------------
# Q. reconcile_status rejection cases
# ---------------------------------------------------------------------------


def test_reconcile_status_rejects_wrong_batch_identity(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    with pytest.raises(PlatformError) as exc:
        reconcile_status("batch_y", job_root)
    assert exc.value.code == "REMOTE_JOB_INTERRUPTED"


def test_reconcile_status_rejects_duplicate_item_key(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    status = _status(batch.batch_id, job_root)
    status["items"].append(status["items"][0])
    _atomic_write_bytes(job_root / "status.json", json.dumps(status).encode("utf-8"))
    with pytest.raises(PlatformError) as exc:
        reconcile_status(batch.batch_id, job_root)
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_reconcile_status_rejects_missing_request_member(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    status = _status(batch.batch_id, job_root)
    status["items"] = []
    _atomic_write_bytes(job_root / "status.json", json.dumps(status).encode("utf-8"))
    with pytest.raises(PlatformError) as exc:
        reconcile_status(batch.batch_id, job_root)
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_reconcile_status_rejects_extra_item_key(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    status = _status(batch.batch_id, job_root)
    status["items"].append({"item_key": "999999", "status": "queued"})
    _atomic_write_bytes(job_root / "status.json", json.dumps(status).encode("utf-8"))
    with pytest.raises(PlatformError) as exc:
        reconcile_status(batch.batch_id, job_root)
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_reconcile_status_rejects_malformed_json(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    (job_root / "status.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        reconcile_status(batch.batch_id, job_root)
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_reconcile_status_rejects_missing_status_file(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    (job_root / "status.json").unlink()
    with pytest.raises(PlatformError) as exc:
        reconcile_status(batch.batch_id, job_root)
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


# ---------------------------------------------------------------------------
# R. module import isolation
# ---------------------------------------------------------------------------


def test_runner_module_import_isolation():
    import sys

    before = set(sys.modules)
    import app.remote_execution.runner  # noqa: F401

    newly_loaded = set(sys.modules) - before
    forbidden = {
        "app.remote_execution.resolver",
        "app.remote_execution.assets",
        "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3",
        "torch",
        "ultralytics",
    }
    assert forbidden.isdisjoint(newly_loaded)


# ---------------------------------------------------------------------------
# Verification helpers shared with the runner contract
# ---------------------------------------------------------------------------


def test_envelope_identity_matches_item(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    envelope = parse_remote_execution_envelope_json(
        (job_root / "results" / "000000" / "envelope.json").read_bytes()
    )
    item = batch.items[0]
    assert envelope.batch_id == batch.batch_id
    assert envelope.item_key == item.item_key
    assert envelope.request_id == item.request_id
    assert envelope.local_run_id == item.local_run_id
    assert envelope.recording_fingerprint == item.recording.expected_recording_fingerprint
    assert envelope.source_data_sha256 == item.recording.expected_source_data_sha256
    assert envelope.pipeline_id == batch.pipeline.id
    assert envelope.pipeline_version == batch.pipeline.version
    assert envelope.orchestrator_commit == item.orchestrator_commit
    assert envelope.remote_runtime_commit == batch.required_remote_runtime_commit
    assert envelope.asset_manifest_sha256 == batch.asset_manifest_sha256
    payload_path = job_root / "results" / "000000" / "analysis_result.zip"
    assert envelope.payload_sha256 == compute_file_sha256(payload_path)


def test_submit_job_writes_initial_status_atomically(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    assert not job_root.exists()
    submit_job(batch, job_root, lambda _bid: None)
    assert job_root.is_dir()
    assert (job_root / "request.json").is_file()
    assert (job_root / "status.json").is_file()
    # No temp files left behind.
    assert not list(job_root.glob("*.tmp"))


def _write_status(batch_id: str, job_root: Path, items: list[dict]) -> None:
    payload = {"batch_id": batch_id, "status": "running", "items": items}
    _atomic_write_bytes(job_root / "status.json", json.dumps(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# 5. spawn failure marks the job interrupted, keeps request frozen, and the
#    same-request retry attaches without spawning again.
# ---------------------------------------------------------------------------


def test_spawn_failure_marks_job_interrupted_and_does_not_leave_queued(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    assert not job_root.exists()

    def failing_spawn(_batch_id):
        raise RuntimeError("synthetic spawn failure")

    with pytest.raises(PlatformError) as exc:
        submit_job(batch, job_root, failing_spawn)
    assert exc.value.code == "REMOTE_JOB_INTERRUPTED"

    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "interrupted"
    assert all(item.status == "interrupted" for item in status.items)
    assert all(item.error_code == "REMOTE_JOB_INTERRUPTED" for item in status.items)

    # request.json remains frozen.
    stored = json.loads((job_root / "request.json").read_text(encoding="utf-8"))
    assert stored["request_sha256"] == batch.request_sha256

    # Same-request retry attaches; interrupted job is terminal, no re-spawn.
    spawned = []
    result = submit_job(batch, job_root, lambda bid: spawned.append(bid))
    assert result == "attached"
    assert spawned == []


# ---------------------------------------------------------------------------
# 7. run_work must reject a wrong status.batch_id before any mutation.
# ---------------------------------------------------------------------------


def test_run_work_rejects_wrong_status_batch_id(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    wrong = {"batch_id": "batch_y", "status": "queued",
             "items": [{"item_key": "000000", "status": "queued"}]}
    _atomic_write_bytes(job_root / "status.json", json.dumps(wrong).encode("utf-8"))
    executor = FakeItemExecutor()
    with pytest.raises(PlatformError) as exc:
        run_work(batch.batch_id, job_root, executor)
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"
    assert executor.execution_count == 0
    # The status file is not silently rewritten.
    stored = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert stored["batch_id"] == "batch_y"


# ---------------------------------------------------------------------------
# 10. crash window: corrupted item does not block later independent items.
# ---------------------------------------------------------------------------


def test_partial_crash_item_does_not_block_later_item(tmp_path):
    batch = _make_batch_two_items()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    # item 000000: running with a partial artifact; item 000001: queued.
    result_dir = job_root / "results" / "000000"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "envelope.json").write_bytes(b"{}")
    _write_status(batch.batch_id, job_root, [
        {"item_key": "000000", "status": "running"},
        {"item_key": "000001", "status": "queued"},
    ])
    executor = FakeItemExecutor()
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 1  # only the second item executes
    status = reconcile_status(batch.batch_id, job_root)
    by_key = {item.item_key: item for item in status.items}
    assert by_key["000000"].status == "interrupted"
    assert by_key["000000"].error_code == "REMOTE_RESULT_CORRUPTED"
    assert by_key["000001"].status == "completed"
    assert status.status == "interrupted"


# ---------------------------------------------------------------------------
# 11. fresh executor publishes incomplete/corrupt result -> item interrupted,
#     later items continue.
# ---------------------------------------------------------------------------


class PartialPublishExecutor:
    """Fake executor that returns normally but publishes only one file."""

    def __init__(self, broken_items=()):
        self.execution_count = 0
        self.broken_items = set(broken_items)

    def execute(self, item, job_root):
        self.execution_count += 1
        if item.item_key in self.broken_items:
            result_dir = job_root / "results" / item.item_key
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / "envelope.json").write_bytes(b"{}")
            return
        FakeItemExecutor().execute(item, job_root)


def test_fresh_executor_partial_publish_marks_item_interrupted_and_continues(tmp_path):
    batch = _make_batch_two_items()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = PartialPublishExecutor(broken_items={"000000"})
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 2
    status = reconcile_status(batch.batch_id, job_root)
    by_key = {item.item_key: item for item in status.items}
    assert by_key["000000"].status == "interrupted"
    assert by_key["000000"].error_code == "REMOTE_RESULT_CORRUPTED"
    assert by_key["000001"].status == "completed"
    assert status.status == "interrupted"


# ---------------------------------------------------------------------------
# 12. unexpected ordinary exception is isolated per item.
# ---------------------------------------------------------------------------


class UnexpectedErrorExecutor:
    def __init__(self, failing_items=()):
        self.execution_count = 0
        self.failing_items = set(failing_items)

    def execute(self, item, job_root):
        self.execution_count += 1
        if item.item_key in self.failing_items:
            raise RuntimeError("synthetic")
        FakeItemExecutor().execute(item, job_root)


def test_unexpected_executor_exception_is_isolated(tmp_path):
    batch = _make_batch_two_items()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = UnexpectedErrorExecutor(failing_items={"000000"})
    run_work(batch.batch_id, job_root, executor)
    assert executor.execution_count == 2
    status = reconcile_status(batch.batch_id, job_root)
    by_key = {item.item_key: item for item in status.items}
    assert by_key["000000"].status == "failed"
    assert by_key["000000"].error_code == "PIPELINE_EXECUTION_FAILED"
    assert by_key["000000"].error_message == "Remote pipeline execution failed."
    # No Python traceback is exposed in status.
    assert "RuntimeError" not in (by_key["000000"].error_message or "")
    assert by_key["000001"].status == "completed"
    assert status.status == "failed"


# ---------------------------------------------------------------------------
# 3. detached worker cwd must be the backend module root.
# ---------------------------------------------------------------------------


def test_default_spawn_worker_uses_backend_cwd(tmp_path, monkeypatch):
    import subprocess as _subprocess

    from app.remote_execution import runner as runner_module

    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        return object()

    monkeypatch.setattr(_subprocess, "Popen", fake_popen)
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    job_root.mkdir(parents=True, exist_ok=True)  # spawn opens log files under job_root
    spawn = runner_module._default_spawn_worker(job_root)
    spawn(batch.batch_id)
    argv = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1] == "-m"
    assert argv[2] == "app.remote_execution.runner"
    assert argv[3] == "work"
    assert "--batch-id" in argv and batch.batch_id in argv
    assert "--job-root" in argv and str(job_root) in argv
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["start_new_session"] is True
    # cwd must be the backend module root (parents[2]), not the repo root.
    expected_backend = Path(runner_module.__file__).resolve().parents[2]
    assert Path(captured["kwargs"]["cwd"]).resolve() == expected_backend