"""Strict remote execution V1 wire schemas.

These models describe the semantic request/result contract between the local
control plane and the detached remote runner. They are transport/provenance
schemas only; there is no ORM entity and no business logic here.
"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitCommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
WireIdentifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")]
LabelSpace = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=128)]

RemoteStatusV1 = Literal["queued", "running", "completed", "failed", "interrupted"]


class RemoteWireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class RemotePipelineRefV1(RemoteWireModel):
    id: WireIdentifier
    version: WireIdentifier


class RemoteRecordingRefV1(RemoteWireModel):
    dataset_name: WireIdentifier
    dataset_split: WireIdentifier
    dataset_key: WireIdentifier
    label_space: LabelSpace
    expected_recording_fingerprint: Sha256Hex
    expected_source_data_sha256: Sha256Hex


class RemoteExecutionRequestV1(RemoteWireModel):
    schema_version: Literal[1] = 1
    request_id: WireIdentifier
    local_run_id: WireIdentifier
    orchestrator_commit: GitCommitSha
    required_remote_runtime_commit: GitCommitSha
    pipeline: RemotePipelineRefV1
    recording: RemoteRecordingRefV1
    parameters: dict[str, Any] = Field(default_factory=dict)
    asset_manifest_sha256: Sha256Hex


class RemoteExecutionItemV1(RemoteWireModel):
    item_key: WireIdentifier
    request_id: WireIdentifier
    local_run_id: WireIdentifier
    orchestrator_commit: GitCommitSha
    recording: RemoteRecordingRefV1
    parameters: dict[str, Any] = Field(default_factory=dict)


class RemoteExecutionBatchV1(RemoteWireModel):
    schema_version: Literal[1] = 1
    batch_id: WireIdentifier
    required_remote_runtime_commit: GitCommitSha
    pipeline: RemotePipelineRefV1
    asset_manifest_sha256: Sha256Hex
    items: Annotated[list[RemoteExecutionItemV1], Field(min_length=1)]
    request_sha256: Sha256Hex

    @model_validator(mode="after")
    def _reject_duplicate_item_keys(self) -> "RemoteExecutionBatchV1":
        keys = [item.item_key for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate item_key in batch")
        return self

    @model_validator(mode="after")
    def _reject_duplicate_local_run_ids(self) -> "RemoteExecutionBatchV1":
        run_ids = [item.local_run_id for item in self.items]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("duplicate local_run_id in batch")
        return self

    @model_validator(mode="after")
    def _reject_duplicate_request_ids(self) -> "RemoteExecutionBatchV1":
        request_ids = [item.request_id for item in self.items]
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("duplicate request_id in batch")
        return self


class RemoteItemStatusV1(RemoteWireModel):
    item_key: WireIdentifier
    status: RemoteStatusV1
    error_code: str | None = None
    error_message: str | None = None
    result_relative_path: str | None = None

    @model_validator(mode="after")
    def _require_safe_relative_result_path(self) -> "RemoteItemStatusV1":
        if self.result_relative_path is not None and not _is_safe_relative_protocol_path(self.result_relative_path):
            raise ValueError("result_relative_path must be a safe relative protocol path")
        return self


class RemoteBatchStatusV1(RemoteWireModel):
    batch_id: WireIdentifier
    status: RemoteStatusV1
    items: list[RemoteItemStatusV1]


class RemoteExecutionEnvelopeV1(RemoteWireModel):
    schema_version: Literal[1] = 1
    request_id: WireIdentifier
    batch_id: WireIdentifier
    item_key: WireIdentifier
    local_run_id: WireIdentifier
    recording_fingerprint: Sha256Hex
    source_data_sha256: Sha256Hex
    pipeline_id: WireIdentifier
    pipeline_version: WireIdentifier
    orchestrator_commit: GitCommitSha
    remote_runtime_commit: GitCommitSha
    asset_manifest_sha256: Sha256Hex
    hardware: dict[str, Any]
    payload_sha256: Sha256Hex
    remote_started_at: datetime | None = None
    remote_finished_at: datetime | None = None


def _is_safe_relative_protocol_path(value: str) -> bool:
    if value != value.strip():
        return False
    if "\x00" in value or "\\" in value or ":" in value:
        return False
    if value.startswith("/"):
        return False
    parts = value.split("/")
    if not parts:
        return False
    return not any(part in ("", ".", "..") for part in parts)


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str):
    raise ValueError(f"non-finite JSON constant in remote execution batch: {value!r}")


def parse_remote_execution_batch_json(raw: bytes | str) -> RemoteExecutionBatchV1:
    """Strict wire parse for a serialized :class:`RemoteExecutionBatchV1`.

    Duplicate JSON keys are rejected at every object nesting level and NaN /
    Infinity / -Infinity JSON constants are rejected. The top-level JSON value
    must be an object. Semantic ``request_sha256`` equality verification is a
    separate concern (``validate_request_sha256``).
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    payload = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_non_finite_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("remote execution batch JSON must be a top-level object")
    return RemoteExecutionBatchV1.model_validate(payload)