"""Detection/localization metrics computed from a match result."""
from __future__ import annotations

from dataclasses import dataclass

from app.evaluation.matching import MatchResult


@dataclass(frozen=True)
class DetectionMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    mean_matched_iou: float | None


def calculate_detection_metrics(
    match_result: MatchResult,
    *,
    gt_count: int,
    prediction_count: int,
) -> DetectionMetrics:
    tp = len(match_result.pairs)
    fp = prediction_count - tp
    fn = gt_count - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    mean_matched_iou = (
        sum(pair.iou for pair in match_result.pairs) / tp if tp else None
    )
    return DetectionMetrics(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        mean_matched_iou=mean_matched_iou,
    )