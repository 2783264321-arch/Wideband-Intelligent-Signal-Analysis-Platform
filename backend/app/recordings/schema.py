from pydantic import BaseModel, ConfigDict


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


class RecordingListRead(BaseModel):
    items: list[RecordingRead]
    total: int
