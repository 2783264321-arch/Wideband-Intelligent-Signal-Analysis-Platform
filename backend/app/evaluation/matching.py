"""Physical time-frequency bbox IoU and one-to-one prediction matching."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment


def bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    intersection_time = max(0.0, min(a["t_end_s"], b["t_end_s"]) - max(a["t_start_s"], b["t_start_s"]))
    intersection_freq = max(0.0, min(a["f_high_hz"], b["f_high_hz"]) - max(a["f_low_hz"], b["f_low_hz"]))
    intersection_area = intersection_time * intersection_freq
    area_a = (a["t_end_s"] - a["t_start_s"]) * (a["f_high_hz"] - a["f_low_hz"])
    area_b = (b["t_end_s"] - b["t_start_s"]) * (b["f_high_hz"] - b["f_low_hz"])
    union = area_a + area_b - intersection_area
    if intersection_area <= 0 or union <= 0:
        return 0.0
    return float(intersection_area / union)


@dataclass(frozen=True)
class MatchPair:
    gt_index: int
    pred_index: int
    iou: float


@dataclass(frozen=True)
class MatchResult:
    pairs: tuple[MatchPair, ...]
    unmatched_gt_indices: tuple[int, ...]
    unmatched_pred_indices: tuple[int, ...]


def match_predictions(
    ground_truths: Sequence[dict[str, Any]],
    predictions: Sequence[dict[str, Any]],
    iou_threshold: float = 0.5,
) -> MatchResult:
    if iou_threshold < 0 or iou_threshold > 1:
        raise ValueError("iou_threshold must be in [0, 1].")
    gt_count = len(ground_truths)
    pred_count = len(predictions)
    if gt_count == 0 or pred_count == 0:
        return MatchResult(
            pairs=(),
            unmatched_gt_indices=tuple(range(gt_count)),
            unmatched_pred_indices=tuple(range(pred_count)),
        )

    iou_matrix = np.zeros((gt_count, pred_count), dtype=np.float64)
    for gt_index, gt in enumerate(ground_truths):
        for pred_index, pred in enumerate(predictions):
            iou_matrix[gt_index, pred_index] = bbox_iou(gt, pred)

    cost = 1.0 - iou_matrix
    gt_indices, pred_indices = linear_sum_assignment(cost)

    pairs: list[MatchPair] = []
    matched_gt: set[int] = set()
    matched_pred: set[int] = set()
    for gt_index, pred_index in zip(gt_indices, pred_indices):
        iou = float(iou_matrix[gt_index, pred_index])
        if iou >= iou_threshold:
            pairs.append(MatchPair(gt_index=int(gt_index), pred_index=int(pred_index), iou=iou))
            matched_gt.add(int(gt_index))
            matched_pred.add(int(pred_index))

    pairs.sort(key=lambda pair: (pair.gt_index, pair.pred_index))
    return MatchResult(
        pairs=tuple(pairs),
        unmatched_gt_indices=tuple(i for i in range(gt_count) if i not in matched_gt),
        unmatched_pred_indices=tuple(i for i in range(pred_count) if i not in matched_pred),
    )