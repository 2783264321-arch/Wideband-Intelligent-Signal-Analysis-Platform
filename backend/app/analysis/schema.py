from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRunCreate(BaseModel):
    recording_id: str
    pipeline_id: str
    executor: str = "local_cpu"
    parameters: dict[str, Any] = Field(default_factory=dict)


class AnalysisRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    recording_id: str
    pipeline_id: str
    pipeline_version: str
    executor: str
    status: str
    parameters_json: dict[str, Any]
    execution_metadata_json: dict[str, Any] | None
    hardware_info_json: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    error_type: str | None
    error_message: str | None
    worker_pid: int | None
    created_at: datetime


class PipelineDefinitionRead(BaseModel):
    id: str
    name: str
    version: str
    label_space: str
    recommended_device: str
    cpu_supported: bool
    stages: list[str]
    inspectable_stages: list[str]
    task_capability: str
    executors_supported: list[str]
    recommended_executor: str


class ExecutorAvailabilityRead(BaseModel):
    executor: str
    available: bool
    reason_code: str | None = None
    reason_message: str | None = None
    remote_profile: str | None = None
    recommended: bool = False
