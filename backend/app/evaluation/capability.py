from dataclasses import dataclass

from app.core.errors import PlatformError


@dataclass(frozen=True)
class ClassificationApplicability:
    applicable: bool
    reason: str | None


def classification_applicability(run, recording, registry) -> ClassificationApplicability:
    if registry is not None:
        try:
            pipeline = registry.get(run.pipeline_id)
        except PlatformError:
            pipeline = None
        if pipeline is not None:
            definition = pipeline.definition
            if definition.task_capability == "detection_localization":
                return ClassificationApplicability(False, "detection_only_pipeline")
            if recording.label_space is not None and definition.label_space != recording.label_space:
                return ClassificationApplicability(False, "label_space_mismatch")
            return ClassificationApplicability(True, None)
    if run.executor == "imported":
        if recording.label_space is None:
            return ClassificationApplicability(False, "unknown_classification_semantics")
        return ClassificationApplicability(True, None)
    return ClassificationApplicability(False, "unknown_classification_semantics")