from pydantic import BaseModel, ConfigDict, Field


class RegisterRecordingPathRequest(BaseModel):
    path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    data_format: str = "complex64_le"
    sample_rate_hz: float = Field(gt=0)
    center_frequency_hz: float
    label_space: str | None = None


class RecordingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    data_format: str
    source: str
    external_path: str | None
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    num_samples: int
    duration_s: float
    dataset_name: str | None
    dataset_split: str | None
    label_space: str | None
    has_ground_truth: bool
    source_data_sha256: str | None
    dataset_id: str | None = None
    sample_key: str | None = None


class RecordingListRead(BaseModel):
    items: list[RecordingRead]
    total: int
