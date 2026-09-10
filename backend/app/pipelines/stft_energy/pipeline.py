from pathlib import Path
from typing import Any

from app.pipelines.base import (
    DetectionPayload,
    ExecutionCapability,
    Pipeline,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.pipelines.plugin import PipelineRuntimeAdapter, PluginDeclaration
from app.pipelines.stft_energy.detector import detect_stft_energy
from app.recordings.reader import read_segment_from_path


def _definition() -> PipelineDefinition:
    return PipelineDefinition(
        id="stft_energy_detector",
        name="STFT Energy Detector",
        version="1.0",
        label_space="signal_presence_v1",
        recommended_device="CPU",
        cpu_supported=True,
        stages=("stft", "noise_floor", "threshold", "morphology", "connected_components", "confidence"),
        inspectable_stages=(),
        task_capability="detection_localization",
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        parameter_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "nperseg": {"type": "integer", "default": 512},
                "noverlap": {"type": "integer", "default": 256},
                "nfft": {"type": "integer", "default": 512},
                "noise_floor_percentile": {"type": "number", "default": 50.0},
                "threshold_margin_db": {"type": "number", "default": 12.0},
                "closing_size": {"type": "integer", "default": 5},
                "min_area": {"type": "integer", "default": 100},
                "min_duration_s": {"type": "number", "default": 0.0},
                "min_bandwidth_hz": {"type": "number", "default": 0.0},
            },
        },
    )


class STFTEnergyDetectorPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return _definition()

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        workspace.mkdir(parents=True, exist_ok=True)
        iq = read_segment_from_path(recording.data_path, recording.data_format)
        regions = detect_stft_energy(
            iq,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            **parameters,
        )
        detections = [
            DetectionPayload(
                t_start_s=region.t_start_s,
                t_end_s=region.t_end_s,
                f_low_hz=region.f_low_hz,
                f_high_hz=region.f_high_hz,
                class_id=0,
                class_name="Signal",
                confidence=region.confidence,
                scores={"detection": region.confidence, "energy_margin_db": region.energy_margin_db},
            )
            for region in regions
        ]
        return PipelineOutput(
            detections=detections,
            artifacts=[],
            run_metadata={
                "kind": "stft_energy_detector",
                "task_capability": "detection_localization",
                "region_count": len(regions),
            },
        )


def create_runtime(*, assets=None, runtime_descriptor=None, output_label_space=None):
    del assets, runtime_descriptor, output_label_space
    return PipelineRuntimeAdapter(STFTEnergyDetectorPipeline())


PLUGIN = PluginDeclaration(
    definition=_definition(),
    runtime_factory_ref="app.pipelines.stft_energy.pipeline:create_runtime",
)
