"""Atomic write-once remote result publication tests (Task 12F-B Task 4).

``publish_result`` exposes BOTH terminal files as one result directory via
staging -> fsync -> same-filesystem directory rename -> parent fsync. A second
publication can never replace an existing final directory. Envelope timestamps
must be timezone-aware UTC and are written exactly. No GPU.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.remote_execution import result_publisher as publisher_module
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.result_ingestor import parse_remote_execution_envelope_json
from app.remote_execution.runner import _verify_terminal_result
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionEnvelopeV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.source_hash import compute_file_sha256

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"
STARTED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)


def _make_batch(batch_id="batch_x", item_key="000000", model_release_id=None):
    pipeline = {"id": "pipeline_x", "version": "1.0"}
    if model_release_id is not None:
        pipeline["model_release_id"] = model_release_id
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=RUNTIME_COMMIT,
        pipeline=pipeline,
        asset_manifest_sha256="c" * 64,
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
            parameters={},
        )],
        request_sha256="0" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _job_root(tmp_path: Path, batch_id="batch_x") -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs / batch_id


def _zip(tmp_path: Path, *, payload=b"synthetic-analysis-result") -> Path:
    path = tmp_path / "analysis_result.zip"
    path.write_bytes(payload)
    return path


def _publish(tmp_path, *, job_root=None, batch=None, zip_path=None, **overrides):
    batch = batch or _make_batch()
    job_root = job_root or _job_root(tmp_path)
    zip_path = zip_path or _zip(tmp_path)
    item = batch.items[0]
    values = dict(
        job_root=job_root,
        item=item,
        batch=batch,
        zip_path=zip_path,
        remote_runtime_commit=RUNTIME_COMMIT,
        asset_manifest_sha256=batch.asset_manifest_sha256,
        hardware={"device": "fake", "device_type": "cuda", "device_index": 0},
        remote_started_at=STARTED_AT,
        remote_finished_at=FINISHED_AT,
    )
    values.update(overrides)
    publisher_module.publish_result(**values)
    return batch, job_root, zip_path


def test_publish_creates_valid_terminal_artifacts(tmp_path):
    batch, job_root, zip_path = _publish(tmp_path)
    result_dir = job_root / "results" / "000000"
    assert (result_dir / "envelope.json").is_file()
    assert (result_dir / "analysis_result.zip").is_file()
    _verify_terminal_result(batch, batch.items[0], job_root)  # must not raise


def test_publish_envelope_identity_matches_frozen_batch(tmp_path):
    batch, job_root, _ = _publish(tmp_path)
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


def test_publish_echoes_model_release_id(tmp_path):
    batch, job_root, _ = _publish(tmp_path, batch=_make_batch(model_release_id="golden"))
    envelope = parse_remote_execution_envelope_json(
        (job_root / "results" / "000000" / "envelope.json").read_bytes()
    )
    assert batch.pipeline.model_release_id == "golden"
    assert envelope.model_release_id == "golden"


def test_publish_legacy_batch_echoes_none_release(tmp_path):
    batch, job_root, _ = _publish(tmp_path)
    envelope = parse_remote_execution_envelope_json(
        (job_root / "results" / "000000" / "envelope.json").read_bytes()
    )
    assert batch.pipeline.model_release_id is None
    assert envelope.model_release_id is None


def test_publish_payload_sha256_is_exact_zip_hash(tmp_path):
    batch, job_root, zip_path = _publish(tmp_path)
    envelope = parse_remote_execution_envelope_json(
        (job_root / "results" / "000000" / "envelope.json").read_bytes()
    )
    assert envelope.payload_sha256 == compute_file_sha256(zip_path)
    staged = job_root / "results" / "000000" / "analysis_result.zip"
    assert envelope.payload_sha256 == compute_file_sha256(staged)


def test_publish_envelope_timestamps_equal_supplied_utc_datetimes(tmp_path):
    batch, job_root, _ = _publish(tmp_path)
    envelope = parse_remote_execution_envelope_json(
        (job_root / "results" / "000000" / "envelope.json").read_bytes()
    )
    assert envelope.remote_started_at == STARTED_AT
    assert envelope.remote_finished_at == FINISHED_AT


@pytest.mark.parametrize("field", ["remote_started_at", "remote_finished_at"])
def test_publish_rejects_naive_datetimes(tmp_path, field):
    job_root = _job_root(tmp_path)
    naive = datetime(2026, 1, 1, 0, 0, 0)
    with pytest.raises(PlatformError) as exc:
        _publish(tmp_path, job_root=job_root, **{field: naive})
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert not (job_root / "results" / "000000").exists()


def test_publish_rejects_non_utc_offset_datetimes(tmp_path):
    job_root = _job_root(tmp_path)
    offset = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    with pytest.raises(PlatformError) as exc:
        _publish(tmp_path, job_root=job_root, remote_started_at=offset)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert not (job_root / "results" / "000000").exists()


def test_no_observer_sees_only_one_terminal_file(tmp_path):
    batch, job_root, _ = _publish(tmp_path)
    result_dir = job_root / "results" / "000000"
    assert (result_dir / "envelope.json").is_file()
    assert (result_dir / "analysis_result.zip").is_file()
    assert len(list(result_dir.iterdir())) == 2


def test_pre_rename_failure_leaves_final_directory_absent(tmp_path, monkeypatch):
    job_root = _job_root(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(publisher_module.os, "replace", boom)
    with pytest.raises(OSError):
        _publish(tmp_path, job_root=job_root)
    assert not (job_root / "results" / "000000").exists()


def test_publish_refuses_to_overwrite_terminal_result(tmp_path):
    batch, job_root, _ = _publish(tmp_path)
    result_dir = job_root / "results" / "000000"
    envelope_first = (result_dir / "envelope.json").read_bytes()
    payload_first = (result_dir / "analysis_result.zip").read_bytes()
    other_zip = _zip(tmp_path, payload=b"different-payload")
    with pytest.raises(PlatformError) as exc:
        _publish(tmp_path, job_root=job_root, zip_path=other_zip)
    assert exc.value.code == "REMOTE_RESULT_CONFLICT"
    assert (result_dir / "envelope.json").read_bytes() == envelope_first
    assert (result_dir / "analysis_result.zip").read_bytes() == payload_first


def test_publish_is_atomic_no_partial_files_on_failure(tmp_path, monkeypatch):
    job_root = _job_root(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(publisher_module.os, "replace", boom)
    with pytest.raises(OSError):
        _publish(tmp_path, job_root=job_root)
    results_dir = job_root / "results"
    if results_dir.exists():
        assert not (results_dir / "000000").exists()
        assert not list(results_dir.glob("*.staging-*"))