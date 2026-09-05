from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.imported_runs.validation import ValidatedAnalysisPackage
from app.recordings.model import RecordingModel


@dataclass(frozen=True)
class BuiltImportedRun:
    run: AnalysisRunModel
    detections: tuple[DetectionResultModel, ...]
    source_detection_ids: dict[str, str]


def build_imported_run_models(
    recording: RecordingModel,
    validated: ValidatedAnalysisPackage,
    *,
    run_id: str,
    detection_ids: list[str],
    batch_import: dict[str, object] | None = None,
) -> BuiltImportedRun:
    if len(detection_ids) != len(validated.detections):
        raise ValueError("detection_ids length must match validated detections")
    models = tuple(
        DetectionResultModel(
            id=detection_id,
            run_id=run_id,
            **item.model_dump(exclude={"id", "scores"}),
            scores_json=item.scores,
        )
        for detection_id, item in zip(detection_ids, validated.detections)
    )
    source_ids = {
        item.id: model.id
        for item, model in zip(validated.detections, models)
        if item.id is not None
    }
    parameters: dict[str, object] = {
        "package": {
            "pipeline_id": validated.manifest.pipeline.id,
            "pipeline_name": validated.manifest.pipeline.name,
            "pipeline_version": validated.manifest.pipeline.version,
            "recording_name": validated.manifest.recording.name,
            "dataset": validated.manifest.recording.dataset,
        },
        "source_detection_ids": source_ids,
        "detection_count": len(models),
    }
    if batch_import is not None:
        parameters["batch_import"] = batch_import
    run = AnalysisRunModel(
        id=run_id,
        recording_id=recording.id,
        pipeline_id=validated.manifest.pipeline.id,
        pipeline_version=validated.manifest.pipeline.version,
        executor="imported",
        status="completed",
        parameters_json=parameters,
        hardware_info_json={
            "executor": validated.manifest.execution.executor,
            "device": validated.manifest.execution.device,
            "environment": validated.manifest.execution.environment,
        },
        created_at=datetime.now(timezone.utc),
    )
    return BuiltImportedRun(run=run, detections=models, source_detection_ids=source_ids)