"""Lightweight shared ZoomSpec frozen definition + remote-only registry stub.

The local control plane must never import torch/ultralytics or construct CPN/FRN
model objects. This module holds the single shared frozen ``PipelineDefinition``
and a remote-only ``Pipeline`` stub that can never execute locally. The real
``ZoomSpecFrozenPipeline`` (Task 12E) is constructed only inside the remote
ItemExecutor (12F-B) and returns the same shared definition.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput

ZOOMSPEC_FROZEN_DEFINITION = PipelineDefinition(
    id="zoomspec_yolo26n_aug_combined_frn_v3",
    name="ZoomSpec YOLO26n + Combined FRN V3",
    version="1.0.0",
    label_space="spacenet_14",
    recommended_device="GPU",
    cpu_supported=False,
    stages=("ls_stft", "cpn", "ahlp", "frn", "postprocess"),
    inspectable_stages=(),
    task_capability="detection_classification",
    executors_supported=("remote_gpu",),
    recommended_executor="remote_gpu",
)


class ZoomSpecRemoteOnlyPipeline(Pipeline):
    """Remote-only registry stub. Never executes locally."""

    @property
    def definition(self) -> PipelineDefinition:
        return ZOOMSPEC_FROZEN_DEFINITION

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        raise PlatformError(
            "EXECUTOR_UNAVAILABLE",
            "ZoomSpec frozen pipeline is remote_gpu only and cannot run locally.",
        )