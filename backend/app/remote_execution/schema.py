"""Strict remote execution V1 wire schemas.

These models describe the semantic request/result contract between the local
control plane and the detached remote runner. They are transport/provenance
schemas only; there is no ORM entity and no business logic here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitCommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Name = Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
LabelSpace = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=128)]

RemoteStatusV1 = Literal["queued", "running", "completed", "failed", "interrupted"]


class RemoteWireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class RemotePipelineRefV1(RemoteWireModel):
    id: Name
    version: Name


class RemoteRecordingRefV1(RemoteWireModel):
    dataset_name: Name
    dataset_split: Name
    dataset_key: Name
    label_space: LabelSpace
    expected_recording_fingerprint: Sha256Hex
    expected_source_data_sha256: Sha256Hex


class RemoteExecutionRequestV1(RemoteWireModel):
    schema_version: Literal[1] = 1
    request_id: Name
    local_run_id: Name
    orchestrator_commit: GitCommitSha
    required_remote_runtime_commit: GitCommitSha
    pipeline: RemotePipelineRefV1
    recording: RemoteRecordingRefV1
    parameters: dict[str, Any] = Field(default_factory=dict)
    asset_manifest_sha256: Sha256Hex


class RemoteExecutionItemV1(RemoteWireModel):
    item_key: Name
    request_id: Name
    local_run_id: Name
    orchestrator_commit: GitCommitSha
    recording: RemoteRecordingRefV1
    parameters: dict[str, Any] = Field(default_factory=dict)


class RemoteExecutionBatchV1(RemoteWireModel):
    schema_version: Literal[1] = 1
    batch_id: Name
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
    item_key: Name
    status: RemoteStatusV1
    error_code: str | None = None
    error_message: str | None = None
    result_relative_path: str | None = None

    @model_validator(mode="after")
    def _reject_absolute_result_path(self) -> "RemoteItemStatusV1":
        if self.result_relative_path and self.result_relative_path.lstrip().startswith(("/", "\\")):
            raise ValueError("result_relative_path must be relative, not absolute")
        return self


class RemoteBatchStatusV1(RemoteWireModel):
    batch_id: Name
    status: RemoteStatusV1
    items: list[RemoteItemStatusV1]


class RemoteExecutionEnvelopeV1(RemoteWireModel):
    schema_version: Literal[1] = 1
    request_id: Name
    batch_id: Name
    item_key: Name
    local_run_id: Name
    recording_fingerprint: Sha256Hex
    source_data_sha256: Sha256Hex
    pipeline_id: Name
    pipeline_version: Name
    orchestrator_commit: GitCommitSha
    remote_runtime_commit: GitCommitSha
    asset_manifest_sha256: Sha256Hex
    hardware: dict[str, Any]
    payload_sha256: Sha256Hex
    remote_started_at: datetime | None = None
    remote_finished_at: datetime | None = None