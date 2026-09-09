"""Shared analysis-result validation and persistence.

``AnalysisResultWriter`` owns the result phase that the local worker and the
future remote result ingestor both use: detection validation, detection row
replacement, artifact-safety checks, metadata publication, and run terminal
state. It never commits or rolls back; the caller owns the transaction.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.core.signal_validation import validate_label, validate_physical_box
from app.detections.model import DetectionResultModel
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineDefinition, PipelineOutput
from app.recordings.model import RecordingModel


class AnalysisResultWriter:
    def __init__(
        self,
        session: Session,
        label_service: LabelSpaceService,
        pipeline_definition: PipelineDefinition,
        workspace: Path,
    ) -> None:
        self.session = session
        self.label_service = label_service
        self.pipeline_definition = pipeline_definition
        self.workspace = workspace

    def persist(
        self,
        run: AnalysisRunModel,
        recording: RecordingModel,
        output: PipelineOutput,
    ) -> None:
        if run.status == "completed":
            raise ValueError("completed AnalysisRun is immutable")
        if run.status in {"failed", "interrupted"}:
            raise ValueError("terminal AnalysisRun is immutable")
        if run.status not in {"pending", "running"}:
            raise ValueError("AnalysisRun is not mutable")

        self.session.execute(delete(DetectionResultModel).where(DetectionResultModel.run_id == run.id))
        for item in output.detections:
            validate_physical_box(
                recording,
                t_start_s=item.t_start_s,
                t_end_s=item.t_end_s,
                f_low_hz=item.f_low_hz,
                f_high_hz=item.f_high_hz,
                error_code="INVALID_DETECTION",
            )
            validate_label(
                self.label_service,
                label_space_id=self.pipeline_definition.label_space,
                class_id=item.class_id,
                class_name=item.class_name,
                error_code="INVALID_DETECTION",
            )
            self.session.add(
                DetectionResultModel(
                    id=f"det_{uuid4().hex}",
                    run_id=run.id,
                    t_start_s=item.t_start_s,
                    t_end_s=item.t_end_s,
                    f_low_hz=item.f_low_hz,
                    f_high_hz=item.f_high_hz,
                    class_id=item.class_id,
                    class_name=item.class_name,
                    confidence=item.confidence,
                    scores_json=item.scores,
                )
            )

        if output.artifacts:
            artifact_index = []
            for artifact in output.artifacts:
                artifact_path = artifact.path.resolve()
                workspace = self.workspace.resolve()
                if workspace not in artifact_path.parents and artifact_path != workspace:
                    raise PlatformError("INVALID_ARTIFACT", "Pipeline artifact path escaped the run workspace.")
                artifact_index.append({**asdict(artifact), "path": str(artifact_path.relative_to(workspace))})
            (self.workspace / "artifacts.json").write_text(
                json.dumps(artifact_index, indent=2, default=str), encoding="utf-8"
            )

        (self.workspace / "run_metadata.json").write_text(
            json.dumps(output.run_metadata, indent=2, default=str), encoding="utf-8"
        )
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)