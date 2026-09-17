from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.data_library.schema import DatasetAnalysisHistoryItemRead


class RegisterSpaceNetRequest(BaseModel):
    dataset_path: str = Field(min_length=1)
    split: str = Field(default="test", pattern=r"^(train|test)$")


class RegistrationSummaryRead(BaseModel):
    created: int
    skipped: int
    invalid: int
    total: int
    dataset_id: str | None = None


class DatasetRead(BaseModel):
    id: str
    name: str
    split: str
    adapter_id: str
    label_space: str | None
    local_root: str
    portable_fingerprint: str | None
    sample_count: int
    ground_truth_sample_count: int
    created_at: datetime


class DatasetListRead(BaseModel):
    items: list[DatasetRead]
    total: int


class DatasetSampleRead(BaseModel):
    id: str
    name: str
    sample_key: str | None
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    num_samples: int
    duration_s: float
    has_ground_truth: bool
    analysis_count: int


class DatasetSampleListRead(BaseModel):
    dataset_id: str
    items: list[DatasetSampleRead]
    total: int


class DatasetAnalysisHistoryListRead(BaseModel):
    dataset_id: str
    items: list[DatasetAnalysisHistoryItemRead]
    total: int
