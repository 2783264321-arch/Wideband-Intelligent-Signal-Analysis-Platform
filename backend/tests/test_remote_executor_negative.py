"""Remote executor negative/error-semantics tests (runner status mapping).

Locks the runner error semantics end to end (CPU/no-GPU): explicit
``PlatformError`` codes raised by an item executor surface as the runner item
status ``failed`` with the same code; generic pipeline exceptions map to
``PIPELINE_EXECUTION_FAILED``; no traceback or local path leaks into the
protocol error message. Terminal-artifact corruption/write-once is covered by
``test_remote_runner.py``.

This file intentionally uses a minimal generic ``ItemExecutor`` stub, not the
retired legacy ``ZoomSpecRemoteItemExecutor``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.runner import create_or_attach, reconcile_status, run_work
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"
MANIFEST_SHA = "c" * 64


def _make_batch(*, parameters=None, batch_id="batch_x", item_key="000000"):
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=COMMIT,
        pipeline={"id": ZOOMSPEC_FROZEN_DEFINITION.id, "version": ZOOMSPEC_FROZEN_DEFINITION.version},
        asset_manifest_sha256=MANIFEST_SHA,
        items=[RemoteExecutionItemV1(
            item_key=item_key,
            request_id="req_1",
            local_run_id="run_x",
            orchestrator_commit=ORCHESTRATOR_COMMIT,
            recording=RemoteRecordingRefV1(
                dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
                label_space="spacenet_14",
                expected_recording_fingerprint="a" * 64,
                expected_source_data_sha256="b" * 64,
            ),
            parameters=parameters or {},
        )],
        request_sha256="0" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _job_root(tmp_path, batch_id="batch_x") -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs / batch_id


class StubExecutor:
    """Minimal generic ItemExecutor that raises a configured exception."""

    def __init__(self, exc=None):
        self._exc = exc

    def execute(self, item, job_root):
        if self._exc is not None:
            raise self._exc


def _run(tmp_path, batch, exc):
    job_root = _job_root(tmp_path, batch.batch_id)
    create_or_attach(batch, job_root)
    run_work(batch.batch_id, job_root, StubExecutor(exc))
    return reconcile_status(batch.batch_id, job_root)


def _assert_no_leak(message, tmp_path):
    assert message is not None
    assert "Traceback" not in message
    assert "File " not in message
    assert str(tmp_path) not in message


def test_request_mismatch_maps_to_item_failed(tmp_path):
    batch = _make_batch(parameters={"x": 1})
    status = _run(tmp_path, batch, PlatformError("REMOTE_REQUEST_INVALID", "bad request"))
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "REMOTE_REQUEST_INVALID"
    _assert_no_leak(status.items[0].error_message, tmp_path)


def test_runtime_mismatch_maps_to_item_failed(tmp_path):
    batch = _make_batch()
    status = _run(
        tmp_path, batch,
        PlatformError("REMOTE_IMPLEMENTATION_MISMATCH", "runtime mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "REMOTE_IMPLEMENTATION_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


def test_asset_mismatch_maps_to_item_failed(tmp_path):
    batch = _make_batch()
    status = _run(
        tmp_path, batch,
        PlatformError("PIPELINE_ASSET_MISMATCH", "asset mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "PIPELINE_ASSET_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


def test_fingerprint_mismatch_maps_to_item_failed(tmp_path):
    batch = _make_batch()
    status = _run(
        tmp_path, batch,
        PlatformError("RECORDING_FINGERPRINT_MISMATCH", "fingerprint mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "RECORDING_FINGERPRINT_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


def test_source_hash_mismatch_maps_to_item_failed(tmp_path):
    batch = _make_batch()
    status = _run(
        tmp_path, batch,
        PlatformError("SOURCE_DATA_HASH_MISMATCH", "source hash mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "SOURCE_DATA_HASH_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


def test_generic_exception_maps_to_pipeline_execution_failed(tmp_path):
    batch = _make_batch()
    status = _run(tmp_path, batch, RuntimeError("synthetic scientific failure"))
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "PIPELINE_EXECUTION_FAILED"
    assert status.items[0].error_message == "Remote pipeline execution failed."
    _assert_no_leak(status.items[0].error_message, tmp_path)
    assert "synthetic scientific failure" not in (status.items[0].error_message or "")


def test_platform_error_keeps_explicit_code(tmp_path):
    batch = _make_batch()
    status = _run(
        tmp_path, batch,
        PlatformError("PIPELINE_EXECUTION_FAILED", "scientific error"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "PIPELINE_EXECUTION_FAILED"
    _assert_no_leak(status.items[0].error_message, tmp_path)
