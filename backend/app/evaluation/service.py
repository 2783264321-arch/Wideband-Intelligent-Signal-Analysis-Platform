"""Compare two completed AnalysisRuns against a Recording's Ground Truth."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.evaluation.matching import match_predictions
from app.evaluation.metrics import (
    calculate_class_aware_metrics,
    calculate_classification_metrics,
    calculate_detection_metrics,
)
from app.evaluation.schema import (
    AlgorithmLabCompareResponse,
    CaseRead,
    ClassAwareMetricsRead,
    ClassificationConfusionRead,
    ClassificationMetricsRead,
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


def _detection_box(d) -> dict:
    return _box(d.t_start_s, d.t_end_s, d.f_low_hz, d.f_high_hz)


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

    def _classification_applicability(self, run: AnalysisRunModel, recording: RecordingModel) -> tuple[bool, str | None]:
        if self.registry is not None:
            try:
                pipeline: Pipeline = self.registry.get(run.pipeline_id)
            except PlatformError:
                pipeline = None
            if pipeline is not None:
                definition = pipeline.definition
                if definition.task_capability == "detection_localization":
                    return False, "detection_only_pipeline"
                if recording.label_space is not None and definition.label_space != recording.label_space:
                    return False, "label_space_mismatch"
                return True, None
        # Imported runs: the M6 importer enforces package.label_space == recording.label_space.
        if run.executor == "imported":
            if recording.label_space is None:
                return False, "unknown_classification_semantics"
            return True, None
        # Neither a registry pipeline nor an imported run: label-space semantics unknown.
        return False, "unknown_classification_semantics"

    def _classification_result(self, run, recording, detections, match_result):
        applicable, reason = self._classification_applicability(run, recording)
        if not applicable:
            return applicable, reason, None, None
        gt_classes = {i: gt.class_id for i, gt in enumerate(self._current_gt)}
        gt_names = {gt.class_id: gt.class_name for gt in self._current_gt}
        pred_classes = {i: d.class_id for i, d in enumerate(detections)}
        pred_names = {d.class_id: d.class_name for d in detections}
        classification = calculate_classification_metrics(
            match_result,
            gt_classes,
            pred_classes,
            gt_class_names=gt_names,
            pred_class_names=pred_names,
        )
        class_aware = calculate_class_aware_metrics(
            match_result,
            gt_count=len(self._current_gt),
            prediction_count=len(detections),
            gt_classes=gt_classes,
            pred_classes=pred_classes,
        )
        return (
            applicable,
            reason,
            ClassificationMetricsRead(
                matched_count=classification.matched_count,
                class_correct=classification.class_correct,
                class_wrong=classification.class_wrong,
                matched_accuracy=classification.matched_accuracy,
                confusions=[
                    ClassificationConfusionRead(
                        gt_class_id=c.gt_class_id,
                        gt_class_name=c.gt_class_name,
                        pred_class_id=c.pred_class_id,
                        pred_class_name=c.pred_class_name,
                        count=c.count,
                    )
                    for c in classification.confusions
                ],
            ),
            ClassAwareMetricsRead(
                tp=class_aware.tp,
                fp=class_aware.fp,
                fn=class_aware.fn,
                precision=class_aware.precision,
                recall=class_aware.recall,
                f1=class_aware.f1,
            ),
        )

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
        self._current_gt = gt_rows

        def _run_results(run: AnalysisRunModel) -> tuple[list[DetectionResultModel], list[dict]]:
            detections = list(
                self.session.scalars(
                    select(DetectionResultModel).where(DetectionResultModel.run_id == run.id).order_by(DetectionResultModel.id)
                ).all()
            )
            boxes = [_detection_box(d) for d in detections]
            return detections, boxes

        detections_a, boxes_a = _run_results(run_a)
        detections_b, boxes_b = _run_results(run_b)
        gt_boxes = [_box(g.t_start_s, g.t_end_s, g.f_low_hz, g.f_high_hz) for g in gt_rows]

        match_a = match_predictions(gt_boxes, boxes_a, iou_threshold)
        match_b = match_predictions(gt_boxes, boxes_b, iou_threshold)
        metrics_a = calculate_detection_metrics(match_a, gt_count=len(gt_rows), prediction_count=len(detections_a))
        metrics_b = calculate_detection_metrics(match_b, gt_count=len(gt_rows), prediction_count=len(detections_b))

        classification_a, reason_a, cls_a, aware_a = self._classification_result(run_a, recording, detections_a, match_a)
        classification_b, reason_b, cls_b, aware_b = self._classification_result(run_b, recording, detections_b, match_b)

        cases = [
            self._build_case(
                gt_index, gt, match_a, detections_a, classification_a, match_b, detections_b, classification_b
            )
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
                classification_applicable=classification_a,
                classification_reason=reason_a,
                classification=cls_a,
                class_aware=aware_a,
            ),
            run_b=RunComparisonRead(
                run_id=run_b.id,
                pipeline_id=run_b.pipeline_id,
                pipeline_name=self._pipeline_name(run_b),
                metrics=DetectionMetricsRead(**vars(metrics_b)),
                classification_applicable=classification_b,
                classification_reason=reason_b,
                classification=cls_b,
                class_aware=aware_b,
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
                    class_id=detection.class_id,
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

    def _build_case(
        self,
        gt_index: int,
        gt,
        match_a,
        detections_a,
        classification_a,
        match_b,
        detections_b,
        classification_b,
    ) -> CaseRead:
        run_a = self._match_for_gt(match_a, gt_index, detections_a)
        run_b = self._match_for_gt(match_b, gt_index, detections_b)
        if classification_a:
            run_a = self._attach_class_correct(run_a, gt, detections_a, match_a)
        if classification_b:
            run_b = self._attach_class_correct(run_b, gt, detections_b, match_b)
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

    @staticmethod
    def _attach_class_correct(run_state, gt, detections, match) -> RunMatchStateRead:
        if not run_state.matched:
            return run_state
        for pair in match.pairs:
            if run_state.detection_id == detections[pair.pred_index].id:
                correct = gt.class_id == detections[pair.pred_index].class_id
                return RunMatchStateRead(
                    matched=True,
                    detection_id=run_state.detection_id,
                    iou=run_state.iou,
                    class_id=run_state.class_id,
                    class_name=run_state.class_name,
                    confidence=run_state.confidence,
                    class_correct=correct,
                    bbox=run_state.bbox,
                )
        return run_state