from pathlib import Path
from typing import Any

from app.pipelines.base import DetectionPayload, Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.stft_energy.detector import detect_stft_energy, read_complex64_le


class STFTEnergyDetectorPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
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
        )

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        workspace.mkdir(parents=True, exist_ok=True)
        if recording.data_format != "complex64_le":
            raise ValueError(f"stft_energy_detector only supports complex64_le, got {recording.data_format}")
        iq = read_complex64_le(recording.data_path)
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