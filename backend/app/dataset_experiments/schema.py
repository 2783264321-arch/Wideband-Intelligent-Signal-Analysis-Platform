from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    dataset_id: str | None = Field(default=None, min_length=1, max_length=64)
    dataset_projection_id: str | None = Field(default=None, min_length=1, max_length=64)
    dataset_name: str | None = Field(default=None, min_length=1)
    dataset_split: str | None = Field(default=None, min_length=1)
    dataset_label_space: str | None = Field(default=None, min_length=1)

    plugin_id: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    model_release_id: Annotated[str, Field(pattern=MODEL_RELEASE_ID_PATTERN)] | None = None

    executor: str | None = None
    execution_mode: Literal["manual", "auto"] = "manual"
    parameters: dict[str, Any] = Field(default_factory=dict)

    evaluation_protocol: str = Field(default=DEFAULT_PHYSICAL_TF_PROTOCOL, min_length=1)
    max_concurrency: int = Field(ge=1)

    @model_validator(mode="after")
    def _require_identity_scope(self) -> "DatasetExperimentCreate":
        has_dataset = self.dataset_id is not None
        has_projection = self.dataset_projection_id is not None
        has_triple = all(
            value is not None
            for value in (self.dataset_name, self.dataset_split, self.dataset_label_space)
        )
        if not has_dataset and not has_projection and not has_triple:
            raise ValueError(
                "Dataset identity requires dataset_id, dataset_projection_id, or the "
                "dataset_name/split/label_space triple."
            )
        return self


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
    latest_analysis_run_id: str | None = None


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
    dataset_projection_id: str | None = None
    dataset_id: str | None = None
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

    # Safe execution-selection provenance projection (never the raw descriptor).
    requested_execution_mode: str | None = None
    auto_reason_code: str | None = None
    auto_reason: str | None = None
    workload_class: str | None = None

    # Derived from Item rows (never persisted)
    expected_items: int
    queued_items: int
    running_items: int
    completed_items: int
    failed_items: int
    attempt_count: int
