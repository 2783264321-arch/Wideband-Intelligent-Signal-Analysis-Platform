"""Detection/localization and classification-aware metrics from a match result."""
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


@dataclass(frozen=True)
class ClassificationConfusion:
    gt_class_id: int
    gt_class_name: str
    pred_class_id: int
    pred_class_name: str
    count: int


@dataclass(frozen=True)
class ClassificationMetrics:
    matched_count: int
    class_correct: int
    class_wrong: int
    matched_accuracy: float | None
    confusions: tuple[ClassificationConfusion, ...]


@dataclass(frozen=True)
class ClassAwareMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


def calculate_classification_metrics(
    match_result: MatchResult,
    gt_classes: dict[int, int],
    pred_classes: dict[int, int],
    *,
    gt_class_names: dict[int, str] | None = None,
    pred_class_names: dict[int, str] | None = None,
) -> ClassificationMetrics:
    """Classification-on-matched metrics.

    Uses only the localization MatchPairs already established. No new matching.
    ``gt_classes`` / ``pred_classes`` map index -> class_id; class names are
    optional for confusion display.
    """
    matched_count = len(match_result.pairs)
    class_correct = 0
    confusion_counts: dict[tuple[int, int], int] = {}
    for pair in match_result.pairs:
        gt_class = gt_classes.get(pair.gt_index)
        pred_class = pred_classes.get(pair.pred_index)
        if gt_class is not None and gt_class == pred_class:
            class_correct += 1
        elif gt_class is not None and pred_class is not None:
            key = (gt_class, pred_class)
            confusion_counts[key] = confusion_counts.get(key, 0) + 1

    matched_accuracy = (class_correct / matched_count) if matched_count else None

    gt_names = gt_class_names or {}
    pred_names = pred_class_names or {}
    confusions = tuple(
        ClassificationConfusion(
            gt_class_id=gt_class,
            gt_class_name=gt_names.get(gt_class, str(gt_class)),
            pred_class_id=pred_class,
            pred_class_name=pred_names.get(pred_class, str(pred_class)),
            count=count,
        )
        for (gt_class, pred_class), count in sorted(confusion_counts.items(), key=lambda item: (item[0][0], item[0][1]))
    )
    return ClassificationMetrics(
        matched_count=matched_count,
        class_correct=class_correct,
        class_wrong=matched_count - class_correct,
        matched_accuracy=matched_accuracy,
        confusions=confusions,
    )


def calculate_class_aware_metrics(
    match_result: MatchResult,
    *,
    gt_count: int,
    prediction_count: int,
    gt_classes: dict[int, int],
    pred_classes: dict[int, int],
) -> ClassAwareMetrics:
    """Class-aware end-to-end metrics derived from the localization pairing.

    A matched pair counts as class-aware TP only when IoU >= threshold AND
    GT class_id == predicted class_id. This is intentionally NOT per-class
    AP/mAP; it reuses the established localization one-to-one assignment.
    """
    class_correct = 0
    for pair in match_result.pairs:
        gt_class = gt_classes.get(pair.gt_index)
        pred_class = pred_classes.get(pair.pred_index)
        if gt_class is not None and gt_class == pred_class:
            class_correct += 1
    tp = class_correct
    fp = prediction_count - class_correct
    fn = gt_count - class_correct
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return ClassAwareMetrics(tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1)