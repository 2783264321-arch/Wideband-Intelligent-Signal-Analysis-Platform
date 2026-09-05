"""Compare two completed AnalysisRuns against a Recording's Ground Truth."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.evaluation.matching import match_predictions
from app.evaluation.metrics import calculate_detection_metrics
from app.evaluation.schema import (
    AlgorithmLabCompareResponse,
    CaseRead,
    DetectionMetricsRead,
    PhysicalBoxRead,
    RunComparisonRead,
    RunMatchStateRead,
)
from app.ground_truth.model import GroundTruthModel
from app.pipelines.base import Pipeline
from app.recordings.model import RecordingModel


def _box(t_start_s, t_end_s, f_low_hz, f_high_hz) -> dict:
    return {"t_start_s": t_start_s, "t_end_s": t_end_s, "f_low_hz": f_low_hz, "f_high_hz": f_high_hz}


class AlgorithmLabComparisonService:
    def __init__(self, session: Session, registry=None):
        self.session = session
        self.registry = registry

    def _pipeline_name(self, run: AnalysisRunModel) -> str:
        if self.registry is not None:
            try:
                pipeline: Pipeline = self.registry.get(run.pipeline_id)
                return pipeline.definition.name
            except PlatformError:
                pass
        package = (run.parameters_json or {}).get("package", {})
        return str(package.get("pipeline_name") or run.pipeline_id)

    def compare(
        self,
        *,
        recording_id: str,
        run_a_id: str,
        run_b_id: str,
        iou_threshold: float = 0.5,
    ) -> AlgorithmLabCompareResponse:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        run_a = self.session.get(AnalysisRunModel, run_a_id)
        run_b = self.session.get(AnalysisRunModel, run_b_id)
        if run_a is None or run_b is None:
            raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)

        for run in (run_a, run_b):
            if run.recording_id != recording_id:
                raise PlatformError("INVALID_COMPARISON", "Analysis run does not belong to the selected Recording.", 422)
            if run.status != "completed":
                raise PlatformError("INVALID_COMPARISON", "Analysis run must be completed before comparison.", 422)

        gt_rows = list(
            self.session.scalars(
                select(GroundTruthModel).where(GroundTruthModel.recording_id == recording_id).order_by(GroundTruthModel.id)
            ).all()
        )
        if not gt_rows:
            raise PlatformError("INVALID_COMPARISON", "Recording has no Ground Truth to compare against.", 422)

        def _run_results(run: AnalysisRunModel) -> tuple[list[DetectionResultModel], list[dict]]:
            detections = list(
                self.session.scalars(
                    select(DetectionResultModel).where(DetectionResultModel.run_id == run.id).order_by(DetectionResultModel.id)
                ).all()
            )
            boxes = [_box(d.t_start_s, d.t_end_s, d.f_low_hz, d.f_high_hz) for d in detections]
            return detections, boxes

        detections_a, boxes_a = _run_results(run_a)
        detections_b, boxes_b = _run_results(run_b)
        gt_boxes = [_box(g.t_start_s, g.t_end_s, g.f_low_hz, g.f_high_hz) for g in gt_rows]

        match_a = match_predictions(gt_boxes, boxes_a, iou_threshold)
        match_b = match_predictions(gt_boxes, boxes_b, iou_threshold)
        metrics_a = calculate_detection_metrics(match_a, gt_count=len(gt_rows), prediction_count=len(detections_a))
        metrics_b = calculate_detection_metrics(match_b, gt_count=len(gt_rows), prediction_count=len(detections_b))

        cases = [
            self._build_case(gt_index, gt, match_a, detections_a, match_b, detections_b)
            for gt_index, gt in enumerate(gt_rows)
        ]

        return AlgorithmLabCompareResponse(
            recording_id=recording_id,
            iou_threshold=iou_threshold,
            run_a=RunComparisonRead(
                run_id=run_a.id,
                pipeline_id=run_a.pipeline_id,
                pipeline_name=self._pipeline_name(run_a),
                metrics=DetectionMetricsRead(**vars(metrics_a)),
            ),
            run_b=RunComparisonRead(
                run_id=run_b.id,
                pipeline_id=run_b.pipeline_id,
                pipeline_name=self._pipeline_name(run_b),
                metrics=DetectionMetricsRead(**vars(metrics_b)),
            ),
            cases=cases,
        )

    @staticmethod
    def _match_for_gt(match, gt_index: int, detections: list[DetectionResultModel]) -> RunMatchStateRead:
        for pair in match.pairs:
            if pair.gt_index == gt_index:
                detection = detections[pair.pred_index]
                return RunMatchStateRead(
                    matched=True,
                    detection_id=detection.id,
                    iou=pair.iou,
                    class_name=detection.class_name,
                    confidence=detection.confidence,
                    bbox=PhysicalBoxRead(
                        t_start_s=detection.t_start_s,
                        t_end_s=detection.t_end_s,
                        f_low_hz=detection.f_low_hz,
                        f_high_hz=detection.f_high_hz,
                    ),
                )
        return RunMatchStateRead(matched=False)

    def _build_case(self, gt_index: int, gt, match_a, detections_a, match_b, detections_b) -> CaseRead:
        run_a = self._match_for_gt(match_a, gt_index, detections_a)
        run_b = self._match_for_gt(match_b, gt_index, detections_b)
        if run_a.matched and run_b.matched:
            comparison = "both_detected"
        elif run_a.matched:
            comparison = "a_only"
        elif run_b.matched:
            comparison = "b_only"
        else:
            comparison = "both_missed"
        return CaseRead(
            ground_truth_id=gt.id,
            class_id=gt.class_id,
            class_name=gt.class_name,
            bbox=PhysicalBoxRead(t_start_s=gt.t_start_s, t_end_s=gt.t_end_s, f_low_hz=gt.f_low_hz, f_high_hz=gt.f_high_hz),
            comparison=comparison,
            run_a=run_a,
            run_b=run_b,
        )