"""Pure request/provenance construction for a single remote ``AnalysisRun``.

This module is construction-only: no file/DB/GroundTruth I/O. It accepts
already-resolved identities (see ``remote_execution/identity.py``) and produces
the canonical ``RemoteExecutionBatchV1`` plus the FROZEN REQUEST METADATA dict.

Two metadata layers are intentionally distinct:

- FROZEN REQUEST METADATA (this module): the semantic request provenance, with
  no ``coordinator_token`` and no ``payload_sha256``.
- FINAL LOCAL EXECUTION METADATA (added later by the coordinator bootstrap): the
  frozen request metadata plus ``coordinator_token``.

``coordinator_token`` and ``payload_sha256`` are never part of the wire batch and
never affect ``request_sha256``.
"""
from __future__ import annotations

from uuid import uuid4

from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemotePipelineRefV1,
    RemoteRecordingRefV1,
)

FROZEN_REQUEST_KEYS = (
    "request_id",
    "batch_id",
    "item_key",
    "local_run_id",
    "request_sha256",
    "orchestrator_commit",
    "required_remote_runtime_commit",
    "asset_manifest_sha256",
    "pipeline_id",
    "pipeline_version",
    "remote_profile",
    "recording_fingerprint",
    "source_data_sha256",
    "dataset_name",
    "dataset_split",
    "dataset_key",
    "label_space",
    "parameters",
)


def _default_id_factory() -> str:
    return f"id_{uuid4().hex}"


def _build_batch_content(metadata: dict, request_sha256: str) -> RemoteExecutionBatchV1:
    item = RemoteExecutionItemV1(
        item_key=metadata["item_key"],
        request_id=metadata["request_id"],
        local_run_id=metadata["local_run_id"],
        orchestrator_commit=metadata["orchestrator_commit"],
        recording=RemoteRecordingRefV1(
            dataset_name=metadata["dataset_name"],
            dataset_split=metadata["dataset_split"],
            dataset_key=metadata["dataset_key"],
            label_space=metadata["label_space"],
            expected_recording_fingerprint=metadata["recording_fingerprint"],
            expected_source_data_sha256=metadata["source_data_sha256"],
        ),
        parameters=dict(metadata["parameters"]),
    )
    return RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=metadata["batch_id"],
        required_remote_runtime_commit=metadata["required_remote_runtime_commit"],
        pipeline=RemotePipelineRefV1(
            id=metadata["pipeline_id"],
            version=metadata["pipeline_version"],
        ),
        asset_manifest_sha256=metadata["asset_manifest_sha256"],
        items=[item],
        request_sha256=request_sha256,
    )


def build_batch(metadata: dict) -> RemoteExecutionBatchV1:
    """Reconstruct an identical RemoteExecutionBatchV1 from metadata alone.

    ``request_sha256`` is recomputed over the canonical payload (which excludes
    ``request_sha256``), so a restart reproduces the exact request identity.
    """
    # Placeholder request_sha256; canonical payload excludes it so the hash is
    # unaffected by the placeholder.
    placeholder = "0" * 64
    provisional = _build_batch_content(metadata, placeholder)
    actual = compute_request_sha256(provisional)
    return _build_batch_content(metadata, actual)


def freeze_request_provenance(
    *,
    local_run_id,
    recording_fingerprint,
    source_data_sha256,
    dataset_name,
    dataset_split,
    dataset_key,
    label_space,
    pipeline_id,
    pipeline_version,
    required_remote_runtime_commit,
    orchestrator_commit,
    asset_manifest_sha256,
    remote_profile,
    id_factory=None,
) -> dict:
    """Pure provenance construction. Returns the FROZEN REQUEST METADATA dict.
    ID factory is injectable for deterministic tests; production uses fresh safe
    UUIDs by default."""
    factory = id_factory or _default_id_factory
    request_id = factory()
    batch_id = factory()
    item_key = factory()
    metadata = {
        "request_id": request_id,
        "batch_id": batch_id,
        "item_key": item_key,
        "local_run_id": local_run_id,
        "orchestrator_commit": orchestrator_commit,
        "required_remote_runtime_commit": required_remote_runtime_commit,
        "asset_manifest_sha256": asset_manifest_sha256,
        "pipeline_id": pipeline_id,
        "pipeline_version": pipeline_version,
        "remote_profile": remote_profile,
        "recording_fingerprint": recording_fingerprint,
        "source_data_sha256": source_data_sha256,
        "dataset_name": dataset_name,
        "dataset_split": dataset_split,
        "dataset_key": dataset_key,
        "label_space": label_space,
        "parameters": {},
    }
    batch = build_batch(metadata)
    metadata["request_sha256"] = batch.request_sha256
    return metadata


def verify_request_sha256(batch: RemoteExecutionBatchV1, metadata: dict) -> bool:
    return compute_request_sha256(batch) == metadata["request_sha256"]