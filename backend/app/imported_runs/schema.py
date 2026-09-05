"""Analysis Package v1 wire contract; coordinates are seconds and absolute Hz."""
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

Name = Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
Number = Annotated[float, Field(allow_inf_nan=False)]


class PackageObject(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class PipelineMetadata(PackageObject):
    id: Name
    name: Name
    version: Name


class RecordingMetadata(PackageObject):
    name: Name
    dataset: str | None = None


class ExecutionMetadata(PackageObject):
    executor: Name
    device: str | None = None
    environment: str | None = None


class ResultPaths(PackageObject):
    detections: Name
    metrics: Name | None = None


class Manifest(PackageObject):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    pipeline: PipelineMetadata
    label_space: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=128)]
    recording: RecordingMetadata
    execution: ExecutionMetadata
    results: ResultPaths
    parameters: dict[str, Any] = Field(default_factory=dict)


class PackageDetection(PackageObject):
    id: Name | None = None
    t_start_s: Number
    t_end_s: Number
    f_low_hz: Number
    f_high_hz: Number
    class_id: int
    class_name: Name
    confidence: Annotated[Number, Field(ge=0, le=1)]
    scores: dict[str, Number] | None = None
