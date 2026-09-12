from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.schema import MODEL_RELEASE_ID_PATTERN
from app.benchmarks.schema import DEFAULT_PHYSICAL_TF_PROTOCOL


class DatasetExperimentCreate(BaseModel):
    """G1 creation request.

    Frozen identity fields (``recording_manifest_hash``,
    ``asset_manifest_sha256``, ``runtime_descriptor_json``) are deliberately
    absent and additionally rejected via ``extra="forbid"``: the platform
    resolves/freezes them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    dataset_name: str = Field(min_length=1)
    dataset_split: str = Field(min_length=1)
    dataset_label_space: str = Field(min_length=1)

    plugin_id: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    model_release_id: Annotated[str, Field(pattern=MODEL_RELEASE_ID_PATTERN)] | None = None

    executor: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

    evaluation_protocol: str = Field(default=DEFAULT_PHYSICAL_TF_PROTOCOL, min_length=1)
    max_concurrency: int = Field(ge=1)


class DatasetExperimentItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_id: str
    manifest_order: int
    recording_id: str
    recording_name: str
    status: str
    last_error_type: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class DatasetExperimentAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_item_id: str
    attempt_number: int
    analysis_run_id: str
    launch_requested_at: datetime | None
    created_at: datetime


class DatasetExperimentRead(BaseModel):
    """Internal read model. Derived counts are computed, never persisted."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str

    dataset_name: str
    dataset_split: str
    dataset_label_space: str
    recording_manifest_hash: str

    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    asset_manifest_sha256: str | None
    parameters_json: dict[str, Any]

    executor: str
    runtime_descriptor_json: dict[str, Any]

    evaluation_protocol: str
    max_concurrency: int

    status: str
    dataset_evaluation_id: str | None

    error_type: str | None
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    # Derived from Item rows (never persisted)
    expected_items: int
    queued_items: int
    running_items: int
    completed_items: int
    failed_items: int
    attempt_count: int
