from collections.abc import Iterable

from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition
from app.pipelines.dummy import DummyPipeline
from app.pipelines.stft_energy.pipeline import STFTEnergyDetectorPipeline


class PipelineRegistry:
    def __init__(self, pipelines: Iterable[Pipeline]):
        self._pipelines = {pipeline.definition.id: pipeline for pipeline in pipelines}

    def list(self) -> list[PipelineDefinition]:
        return [self._pipelines[key].definition for key in sorted(self._pipelines)]

    def get(self, pipeline_id: str) -> Pipeline:
        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            raise PlatformError("PIPELINE_INCOMPATIBLE", f"Pipeline '{pipeline_id}' is not registered.")
        return pipeline


def create_pipeline_registry() -> PipelineRegistry:
    return PipelineRegistry([DummyPipeline(), STFTEnergyDetectorPipeline()])
