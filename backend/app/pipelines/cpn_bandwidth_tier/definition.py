"""Lightweight shared CPN bandwidth-tier plugin definition.

Importing this module must stay ML-free: it only describes the plugin identity,
the SpaceNet input compatibility, and the ``cpn_bandwidth_tier_v1`` output label
space. The scientific runtime is loaded lazily by the plugin factory.
"""
from __future__ import annotations

from app.pipelines.base import ExecutionCapability, PipelineDefinition

CPN_BANDWIDTH_TIER_DEFINITION = PipelineDefinition(
    id="cpn_bandwidth_tier",
    name="CPN Bandwidth Tier",
    version="1.0.0",
    label_space="cpn_bandwidth_tier_v1",
    recommended_device="GPU",
    cpu_supported=False,
    stages=("ls_stft", "cpn", "bandwidth_tier"),
    inspectable_stages=(),
    task_capability="detection_classification",
    executors_supported=("remote_gpu",),
    recommended_executor="remote_gpu",
    input_compatibility=("spacenet_14",),
    output_label_space="cpn_bandwidth_tier_v1",
    dataset_adapters=("SpaceNet",),
    model_release_required=True,
    technical_execution_capabilities=(
        ExecutionCapability("remote_gpu", "cuda", "float16"),
        ExecutionCapability("local_cpu", "cpu", "float32"),
    ),
    recommended_execution="remote_gpu",
)
