from pathlib import Path
from typing import Any

from app.pipelines.base import DetectionPayload, Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.plugin import PipelineRuntimeAdapter, PluginDeclaration


def _definition() -> PipelineDefinition:
    return PipelineDefinition(
        id="dummy",
        name="Dummy Pipeline",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=("input", "deterministic_detection"),
        inspectable_stages=(),
    )


class DummyPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return _definition()

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        del parameters
        workspace.mkdir(parents=True, exist_ok=True)
        frequency_span = recording.frequency_high_hz - recording.frequency_low_hz
        detection = DetectionPayload(
            t_start_s=recording.duration_s * 0.2,
            t_end_s=recording.duration_s * 0.8,
            f_low_hz=recording.center_frequency_hz - frequency_span * 0.1,
            f_high_hz=recording.center_frequency_hz + frequency_span * 0.1,
            class_id=6,
            class_name="BLE LE1M",
            confidence=0.90,
            scores={"detection": 0.90, "classification": 0.90},
        )
        return PipelineOutput(
            detections=[detection],
            artifacts=[],
            run_metadata={"kind": "deterministic_test_pipeline"},
        )


def create_runtime(*, assets=None, runtime_descriptor=None, output_label_space=None):
    del assets, runtime_descriptor, output_label_space
    return PipelineRuntimeAdapter(DummyPipeline())


PLUGIN = PluginDeclaration(
    definition=_definition(),
    runtime_factory_ref="app.pipelines.dummy:create_runtime",
)
