"""Atomic write-once remote result publication (Task 12F-B Task 4).

Publishes BOTH terminal files as one result directory:

    <job_root>/results/<item_key>/{envelope.json, analysis_result.zip}

Staging is created on the SAME filesystem; both files are fsynced; the staging
directory is atomically renamed into place; the parent is fsynced. A crash
before the rename leaves no terminal result visible; a crash after the rename
exposes both terminal files. A second publication can never replace an existing
final result directory (write-once refusal).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import tempfile

from app.core.errors import PlatformError
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionEnvelopeV1,
    RemoteExecutionItemV1,
)
from app.remote_execution.source_hash import compute_file_sha256

_ENVELOPE_FILENAME = "envelope.json"
_PAYLOAD_FILENAME = "analysis_result.zip"


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _require_utc_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlatformError("REMOTE_RESULT_INVALID", f"{name} must be a timezone-aware UTC datetime.")
    if value.utcoffset() != timedelta(0):
        raise PlatformError("REMOTE_RESULT_INVALID", f"{name} must be a UTC datetime.")


def publish_result(
    *,
    job_root: Path,
    item: RemoteExecutionItemV1,
    batch: RemoteExecutionBatchV1,
    zip_path: Path,
    remote_runtime_commit: str,
    asset_manifest_sha256: str,
    hardware: dict,
    remote_started_at: datetime,
    remote_finished_at: datetime,
) -> None:
    """Atomically expose BOTH terminal files as one result directory."""
    _require_utc_aware(remote_started_at, "remote_started_at")
    _require_utc_aware(remote_finished_at, "remote_finished_at")

    job_root = Path(job_root)
    results_dir = job_root / "results"
    final_dir = results_dir / item.item_key
    if final_dir.exists():
        raise PlatformError(
            "REMOTE_RESULT_CONFLICT",
            "Terminal result directory already exists; refusing to overwrite.",
        )

    payload_sha256 = compute_file_sha256(Path(zip_path))
    envelope = RemoteExecutionEnvelopeV1(
        schema_version=1,
        request_id=item.request_id,
        batch_id=batch.batch_id,
        item_key=item.item_key,
        local_run_id=item.local_run_id,
        recording_fingerprint=item.recording.expected_recording_fingerprint,
        source_data_sha256=item.recording.expected_source_data_sha256,
        pipeline_id=batch.pipeline.id,
        pipeline_version=batch.pipeline.version,
        orchestrator_commit=item.orchestrator_commit,
        remote_runtime_commit=remote_runtime_commit,
        asset_manifest_sha256=asset_manifest_sha256,
        hardware=dict(hardware),
        payload_sha256=payload_sha256,
        remote_started_at=remote_started_at,
        remote_finished_at=remote_finished_at,
    )

    results_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{item.item_key}.staging-", dir=results_dir))
    try:
        shutil.copyfile(Path(zip_path), staging / _PAYLOAD_FILENAME)
        (staging / _ENVELOPE_FILENAME).write_bytes(
            envelope.model_dump_json().encode("utf-8")
        )
        _fsync_file(staging / _PAYLOAD_FILENAME)
        _fsync_file(staging / _ENVELOPE_FILENAME)
        _fsync_dir(staging)
        os.replace(staging, final_dir)
        _fsync_dir(results_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise