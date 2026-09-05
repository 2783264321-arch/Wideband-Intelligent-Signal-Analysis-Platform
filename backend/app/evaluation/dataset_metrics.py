"""Dataset-level operating-point diagnostics derived from M8.5 Hungarian matching.

AP/mAP is intentionally NOT computed here. This module aggregates the existing
class-agnostic one-to-one matching at IoU 0.5 over many Recordings.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.matching import match_predictions
from app.evaluation.metrics import ClassificationConfusion, calculate_detection_metrics


@dataclass(frozen=True)
class EvaluationSample:
    recording_id: str
    manifest_order: int
    ground_truths: tuple[EvaluationGroundTruth, ...]
    predictions: tuple[EvaluationPrediction, ...]


@dataclass(frozen=True)
class OperatingMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class MatchedClassificationDiagnostics:
    matched_count: int
    class_correct: int
    class_wrong: int
    matched_accuracy: float | None
    confusions: tuple[ClassificationConfusion, ...]


@dataclass(frozen=True)
class PerClassOperatingMetrics:
    class_id: int
    class_name: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class DatasetDiagnostics:
    localization: OperatingMetrics
    classification: MatchedClassificationDiagnostics | None
    class_aware: OperatingMetrics | None
    per_class: tuple[PerClassOperatingMetrics, ...]


def _to_box(obj) -> dict:
    return {
        "t_start_s": obj.t_start_s,
        "t_end_s": obj.t_end_s,
        "f_low_hz": obj.f_low_hz,
        "f_high_hz": obj.f_high_hz,
    }


def compute_dataset_diagnostics(
    samples: list[EvaluationSample],
    *,
    classification_applicable: bool,
) -> DatasetDiagnostics:
    localization_tp = 0
    localization_fp = 0
    localization_fn = 0
    matched_count = 0
    class_correct = 0
    confusion_counts: dict[tuple[int, int], int] = {}
    class_names: dict[int, str] = {}
    class_aware_tp = 0
    per_class_tp: dict[int, int] = {}
    per_class_fp: dict[int, int] = {}
    per_class_fn: dict[int, int] = {}

    for sample in samples:
        gt_boxes = [_to_box(gt) for gt in sample.ground_truths]
        pred_boxes = [_to_box(pred) for pred in sample.predictions]
        match = match_predictions(gt_boxes, pred_boxes, iou_threshold=0.5)
        detection = calculate_detection_metrics(
            match, gt_count=len(sample.ground_truths), prediction_count=len(sample.predictions)
        )
        localization_tp += detection.tp
        localization_fp += detection.fp
        localization_fn += detection.fn

        if classification_applicable:
            matched_count += len(match.pairs)
            for pair in match.pairs:
                gt = sample.ground_truths[pair.gt_index]
                pred = sample.predictions[pair.pred_index]
                class_names[gt.class_id] = gt.class_name
                class_names[pred.class_id] = pred.class_name
                if gt.class_id == pred.class_id:
                    class_correct += 1
                    class_aware_tp += 1
                    per_class_tp[gt.class_id] = per_class_tp.get(gt.class_id, 0) + 1
                else:
                    key = (gt.class_id, pred.class_id)
                    confusion_counts[key] = confusion_counts.get(key, 0) + 1
                    per_class_fp[pred.class_id] = per_class_fp.get(pred.class_id, 0) + 1
                    per_class_fn[gt.class_id] = per_class_fn.get(gt.class_id, 0) + 1
            for pred_index in match.unmatched_pred_indices:
                pred = sample.predictions[pred_index]
                class_names[pred.class_id] = pred.class_name
                per_class_fp[pred.class_id] = per_class_fp.get(pred.class_id, 0) + 1
            for gt_index in match.unmatched_gt_indices:
                gt = sample.ground_truths[gt_index]
                class_names[gt.class_id] = gt.class_name
                per_class_fn[gt.class_id] = per_class_fn.get(gt.class_id, 0) + 1

    localization = OperatingMetrics(
        tp=localization_tp, fp=localization_fp, fn=localization_fn,
        precision=_precision(localization_tp, localization_fp),
        recall=_precision(localization_tp, localization_fn),
        f1=_f1(localization_tp, localization_fp, localization_fn),
    )

    if not classification_applicable:
        return DatasetDiagnostics(
            localization=localization, classification=None, class_aware=None, per_class=(),
        )

    confusions = tuple(
        ClassificationConfusion(
            gt_class_id=gt_class, gt_class_name=class_names.get(gt_class, str(gt_class)),
            pred_class_id=pred_class, pred_class_name=class_names.get(pred_class, str(pred_class)),
            count=count,
        )
        for (gt_class, pred_class), count in sorted(confusion_counts.items(), key=lambda item: (item[0][0], item[0][1]))
    )
    classification = MatchedClassificationDiagnostics(
        matched_count=matched_count,
        class_correct=class_correct,
        class_wrong=matched_count - class_correct,
        matched_accuracy=(class_correct / matched_count) if matched_count else None,
        confusions=confusions,
    )

    total_pred = sum(len(sample.predictions) for sample in samples)
    total_gt = sum(len(sample.ground_truths) for sample in samples)
    class_aware = OperatingMetrics(
        tp=class_aware_tp,
        fp=total_pred - class_aware_tp,
        fn=total_gt - class_aware_tp,
        precision=_precision(class_aware_tp, total_pred - class_aware_tp),
        recall=_precision(class_aware_tp, total_gt - class_aware_tp),
        f1=_f1(class_aware_tp, total_pred - class_aware_tp, total_gt - class_aware_tp),
    )

    per_class = tuple(
        PerClassOperatingMetrics(
            class_id=class_id,
            class_name=class_names.get(class_id, str(class_id)),
            tp=per_class_tp.get(class_id, 0),
            fp=per_class_fp.get(class_id, 0),
            fn=per_class_fn.get(class_id, 0),
            precision=_precision(per_class_tp.get(class_id, 0), per_class_fp.get(class_id, 0)),
            recall=_precision(per_class_tp.get(class_id, 0), per_class_fn.get(class_id, 0)),
            f1=_f1(per_class_tp.get(class_id, 0), per_class_fp.get(class_id, 0), per_class_fn.get(class_id, 0)),
        )
        for class_id in sorted(class_names)
    )
    return DatasetDiagnostics(
        localization=localization, classification=classification, class_aware=class_aware, per_class=per_class,
    )


def _precision(tp: int, positive: int) -> float:
    return tp / (tp + positive) if (tp + positive) else 0.0


def _f1(tp: int, fp: int, fn: int) -> float:
    precision = _precision(tp, fp)
    recall = _precision(tp, fn)
    return 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0