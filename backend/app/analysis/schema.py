from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# ModelRelease logical id contract (mirrors ModelReleaseStore record validation).
# Explicit API-provided ids are validated here before service/store resolution;
# ``None`` keeps the platform default.
MODEL_RELEASE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,127}$"


class RemoteExecutionMetadataRead(BaseModel):
    """PUBLIC allowlist of remote execution metadata exposed to API clients.

    Internal provenance (coordinator_token, request/batch/item ids,
    request_sha256, fingerprints, source hash, orchestrator commit) is never
    serialized; the DB retains the full internal metadata for coordinator /
    recovery internals. Unknown internal keys are ignored (not rejected) so a
    completed/terminal run still serializes cleanly.
    """

    model_config = ConfigDict(extra="ignore")

    remote_profile: str | None = None
    required_remote_runtime_commit: str | None = None
    remote_runtime_commit: str | None = None
    payload_sha256: str | None = None
    remote_started_at: datetime | None = None
    remote_finished_at: datetime | None = None


class AnalysisRunCreate(BaseModel):
    recording_id: str
    pipeline_id: str
    executor: str = "local_cpu"
    parameters: dict[str, Any] = Field(default_factory=dict)
    model_release_id: Annotated[str, Field(pattern=MODEL_RELEASE_ID_PATTERN)] | None = None


class AnalysisRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    recording_id: str
    pipeline_id: str
    pipeline_version: str
    executor: str
    status: str
    parameters_json: dict[str, Any]
    execution_metadata_json: RemoteExecutionMetadataRead | None
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
    plugin_api_version: int = 1
    output_label_space: str
    input_compatibility: list[str] = []
    dataset_adapters: list[str] = []
    model_release_required: bool = False
    technical_execution_capabilities: list[dict] = []
    recommended_execution: str | None = None


class ExecutorAvailabilityRead(BaseModel):
    executor: str
    available: bool
    reason_code: str | None = None
    reason_message: str | None = None
    remote_profile: str | None = None
    recommended: bool = False
