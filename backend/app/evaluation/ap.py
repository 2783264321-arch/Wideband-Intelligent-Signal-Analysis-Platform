"""Deterministic confidence-ranked physical time-frequency AP/mAP protocol.

AP uses greedy matching ranked by confidence descending, NOT Hungarian matching.
Coordinates stay in physical seconds and absolute Hz.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.evaluation.matching import bbox_iou

IOU_THRESHOLDS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))


@dataclass(frozen=True)
class EvaluationGroundTruth:
    recording_id: str
    manifest_order: int
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class EvaluationPrediction:
    recording_id: str
    manifest_order: int
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
    confidence: float


@dataclass(frozen=True)
class AveragePrecisionResult:
    ap: float | None
    gt_count: int
    prediction_count: int


@dataclass(frozen=True)
class APSummary:
    ap50: float | None
    ap50_95: float | None
    per_threshold: tuple[tuple[float, float | None], ...]


@dataclass(frozen=True)
class PerClassAP:
    class_id: int
    class_name: str
    gt_count: int
    prediction_count: int
    ap50: float | None
    ap50_95: float | None


@dataclass(frozen=True)
class ClassAwareAPSummary:
    map50: float | None
    map50_95: float | None
    per_class: tuple[PerClassAP, ...]


def prediction_sort_key(pred: EvaluationPrediction):
    return (
        -pred.confidence,
        pred.manifest_order,
        pred.t_start_s,
        pred.f_low_hz,
        pred.t_end_s,
        pred.f_high_hz,
        pred.class_id,
    )


def _gt_sort_key(gt: EvaluationGroundTruth):
    return (
        gt.manifest_order,
        gt.t_start_s,
        gt.f_low_hz,
        gt.t_end_s,
        gt.f_high_hz,
        gt.class_id,
        gt.class_name,
    )


def _as_box(obj) -> dict:
    return {
        "t_start_s": obj.t_start_s,
        "t_end_s": obj.t_end_s,
        "f_low_hz": obj.f_low_hz,
        "f_high_hz": obj.f_high_hz,
    }


def average_precision_at_iou(
    ground_truths: list[EvaluationGroundTruth],
    predictions: list[EvaluationPrediction],
    *,
    iou_threshold: float,
    class_id: int | None,
) -> AveragePrecisionResult:
    if class_id is None:
        eligible_gt = list(ground_truths)
        eligible_pred = list(predictions)
    else:
        eligible_gt = [gt for gt in ground_truths if gt.class_id == class_id]
        eligible_pred = [pred for pred in predictions if pred.class_id == class_id]

    # Canonical GT ordering makes greedy assignment independent of DB row
    # insertion order, so the same semantic dataset yields the same AP across
    # machines. Local IDs / UUIDs / row order never participate.
    eligible_gt.sort(key=_gt_sort_key)

    if not eligible_gt:
        return AveragePrecisionResult(ap=None, gt_count=0, prediction_count=len(eligible_pred))
    if not eligible_pred:
        return AveragePrecisionResult(ap=0.0, gt_count=len(eligible_gt), prediction_count=0)

    gt_by_recording: dict[str, list[int]] = {}
    for index, gt in enumerate(eligible_gt):
        gt_by_recording.setdefault(gt.recording_id, []).append(index)

    ranked = sorted(eligible_pred, key=prediction_sort_key)
    matched_gt: set[int] = set()
    tp_fp: list[tuple[int, int]] = []  # (tp, fp) cumulative operating points
    tp_count = 0
    fp_count = 0
    for pred in ranked:
        best_index = None
        best_iou = -1.0
        for gt_index in gt_by_recording.get(pred.recording_id, []):
            if gt_index in matched_gt:
                continue
            gt = eligible_gt[gt_index]
            iou = bbox_iou(_as_box(gt), _as_box(pred))
            if iou > best_iou:
                best_iou = iou
                best_index = gt_index
        if best_index is not None and best_iou >= iou_threshold:
            tp_count += 1
            matched_gt.add(best_index)
        else:
            fp_count += 1
        tp_fp.append((tp_count, fp_count))

    total_gt = len(eligible_gt)
    # 101-point interpolated precision.
    recall_points = [i / 100.0 for i in range(101)]
    interpolated = []
    for target in recall_points:
        best_precision = 0.0
        for tp, fp in tp_fp:
            recall = tp / total_gt
            if recall >= target:
                precision = tp / (tp + fp) if (tp + fp) else 0.0
                if precision > best_precision:
                    best_precision = precision
        interpolated.append(best_precision)
    ap = sum(interpolated) / len(interpolated)
    return AveragePrecisionResult(ap=ap, gt_count=total_gt, prediction_count=len(eligible_pred))


def _ap_pair(ground_truths, predictions, iou_threshold):
    result = average_precision_at_iou(ground_truths, predictions, iou_threshold=iou_threshold, class_id=None)
    return result.ap


def localization_ap_summary(
    ground_truths: list[EvaluationGroundTruth],
    predictions: list[EvaluationPrediction],
) -> APSummary:
    per_threshold = []
    for threshold in IOU_THRESHOLDS:
        result = average_precision_at_iou(ground_truths, predictions, iou_threshold=threshold, class_id=None)
        per_threshold.append((threshold, result.ap))
    ap50 = per_threshold[0][1]
    ap_values = [value for _, value in per_threshold]
    ap50_95 = (sum(value for value in ap_values if value is not None) / len(ap_values)) if any(
        value is not None for value in ap_values) else None
    return APSummary(ap50=ap50, ap50_95=ap50_95, per_threshold=tuple(per_threshold))


def class_aware_ap_summary(
    ground_truths: list[EvaluationGroundTruth],
    predictions: list[EvaluationPrediction],
) -> ClassAwareAPSummary:
    class_ids = {gt.class_id for gt in ground_truths} | {pred.class_id for pred in predictions}
    class_names = {gt.class_id: gt.class_name for gt in ground_truths}
    class_names.update({pred.class_id: pred.class_name for pred in predictions})

    per_class = []
    for class_id in sorted(class_ids):
        gts = [gt for gt in ground_truths if gt.class_id == class_id]
        preds = [pred for pred in predictions if pred.class_id == class_id]
        per_threshold = []
        for threshold in IOU_THRESHOLDS:
            result = average_precision_at_iou(gts, preds, iou_threshold=threshold, class_id=class_id)
            per_threshold.append((threshold, result.ap))
        per_class.append(PerClassAP(
            class_id=class_id,
            class_name=class_names.get(class_id, str(class_id)),
            gt_count=len(gts),
            prediction_count=len(preds),
            ap50=per_threshold[0][1],
            ap50_95=(sum(v for _, v in per_threshold if v is not None) / len(per_threshold))
            if any(v is not None for _, v in per_threshold) else None,
        ))

    valid = [item for item in per_class if item.gt_count > 0]
    map50 = (sum(item.ap50 for item in valid if item.ap50 is not None) / len(valid)) if valid else None
    map50_95 = (sum(item.ap50_95 for item in valid if item.ap50_95 is not None) / len(valid)) if valid else None
    return ClassAwareAPSummary(map50=map50, map50_95=map50_95, per_class=tuple(per_class))