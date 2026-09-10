from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

PLUGIN_API_VERSION = 1


@dataclass(frozen=True)
class ExecutionCapability:
    """A plugin's technical execution claim (not a certification)."""

    executor: str
    device_type: str
    precision: str

    def key(self) -> tuple[str, str, str]:
        return (self.executor, self.device_type, self.precision)


@dataclass(frozen=True)
class PipelineDefinition:
    id: str
    name: str
    version: str
    label_space: str
    recommended_device: str
    cpu_supported: bool
    stages: tuple[str, ...]
    inspectable_stages: tuple[str, ...]
    task_capability: str = "classification"
    executors_supported: tuple[str, ...] = ("local_cpu",)
    recommended_executor: str = "local_cpu"
    plugin_api_version: int = PLUGIN_API_VERSION
    parameter_schema: Mapping[str, Any] = field(default_factory=dict)
    technical_execution_capabilities: tuple[ExecutionCapability, ...] = ()
    recommended_execution: str | None = None
    model_release_required: bool = False
    input_compatibility: tuple[str, ...] = ()
    output_label_space: str | None = None
    dataset_adapters: tuple[str, ...] = ()

    @property
    def plugin_id(self) -> str:
        return self.id

    @property
    def plugin_version(self) -> str:
        return self.version

    @property
    def resolved_output_label_space(self) -> str:
        return self.output_label_space or self.label_space


@dataclass(frozen=True)
class RecordingInput:
    id: str
    data_path: Path
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    duration_s: float
    label_space: str | None


@dataclass(frozen=True)
class DetectionPayload:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
    confidence: float
    scores: dict[str, float] | None = None


@dataclass(frozen=True)
class ArtifactPayload:
    stage_name: str
    artifact_type: str
    scope: str
    path: Path
    detection_index: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class PipelineOutput:
    detections: list[DetectionPayload] = field(default_factory=list)
    artifacts: list[ArtifactPayload] = field(default_factory=list)
    run_metadata: dict[str, Any] = field(default_factory=dict)


class Pipeline(ABC):
    @property
    @abstractmethod
    def definition(self) -> PipelineDefinition:
        raise NotImplementedError

    @abstractmethod
    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        raise NotImplementedError
