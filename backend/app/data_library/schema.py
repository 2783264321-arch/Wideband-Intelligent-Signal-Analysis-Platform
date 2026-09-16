from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DatasetProjectionSummaryRead(BaseModel):
    dataset_projection_id: str
    source: str
    dataset_name: str
    dataset_split: str
    label_space: str | None
    sample_count: int
    ground_truth_sample_count: int
    external: bool
    source_location: str | None


class DatasetProjectionListRead(BaseModel):
    items: list[DatasetProjectionSummaryRead]
    total: int


class DatasetSampleRead(BaseModel):
    id: str
    name: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    duration_s: float
    has_ground_truth: bool
    analysis_count: int
    sample_rate_derived: bool
    center_frequency_derived: bool


class DatasetSampleListRead(BaseModel):
    dataset_projection_id: str
    items: list[DatasetSampleRead]
    total: int


class StandaloneSampleRead(BaseModel):
    id: str
    name: str
    source: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    duration_s: float
    data_format: str
    has_ground_truth: bool
    analysis_count: int


class StandaloneSampleListRead(BaseModel):
    items: list[StandaloneSampleRead]
    total: int


class DatasetAnalysisHistoryItemRead(BaseModel):
    kind: Literal["experiment", "evaluation", "imported_batch"]
    resource_id: str
    name: str
    pipeline_id: str
    pipeline_version: str
    status: str
    executor: str | None
    expected_items: int
    completed_items: int
    failed_items: int
    coverage: float | None
    created_at: datetime | None
    dataset_evaluation_id: str | None = None
    batch_id: str | None = None
    archive_sha256: str | None = None


class DatasetAnalysisHistoryListRead(BaseModel):
    dataset_projection_id: str
    items: list[DatasetAnalysisHistoryItemRead]
    total: int
