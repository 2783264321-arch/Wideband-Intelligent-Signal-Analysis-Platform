"""M9.1 Task 12E — frozen postprocess: score threshold + class-aware TF NMS.

Pure post-FRN scientific postprocessing for the frozen
``zoomspec_yolo26n_aug_combined_frn_v3`` pipeline.

Semantics reproduce the historical frozen postprocess exactly:
1. retain detections with ``confidence >= 0.001``;
2. physical class-aware greedy NMS at TF-IoU threshold ``0.7``.

For one Recording (all detections share one Recording), NMS grouping is
effectively by ``class_id``; different classes never suppress each other.
Within a class the candidates are sorted by a stable Python sort, descending
confidence; greedy keep: a remaining candidate survives only if
``tf_iou(best, candidate) < 0.7``. The final kept detections are returned by a
stable global score-descending sort.

Frozen parameters are internal constants and are NOT caller-configurable.
"""
from __future__ import annotations

from typing import Sequence

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefinedDetection

_SCORE_THRESHOLD = 0.001
_NMS_IOU = 0.7


def tf_iou(a: FRNRefinedDetection, b: FRNRefinedDetection) -> float:
    """Physical time-frequency IoU in (seconds x Hz), no pixel ``+1``."""
    dt = max(0.0, min(a.t_end_s, b.t_end_s) - max(a.t_start_s, b.t_start_s))
    df = max(0.0, min(a.f_high_hz, b.f_high_hz) - max(a.f_low_hz, b.f_low_hz))
    intersection = dt * df
    area_a = (a.t_end_s - a.t_start_s) * (a.f_high_hz - a.f_low_hz)
    area_b = (b.t_end_s - b.t_start_s) * (b.f_high_hz - b.f_low_hz)
    union = area_a + area_b - intersection
    return 0.0 if union <= 0 else float(intersection / union)


def _group_physical_class_nms(detections: list[FRNRefinedDetection]) -> list[FRNRefinedDetection]:
    """Greedy class-aware NMS within one class group (stable score-desc)."""
    kept: list[FRNRefinedDetection] = []
    candidates = sorted(detections, key=lambda item: item.confidence, reverse=True)
    while candidates:
        best = candidates.pop(0)
        kept.append(best)
        candidates = [item for item in candidates if tf_iou(best, item) < _NMS_IOU]
    return kept


def physical_class_nms(detections: Sequence[FRNRefinedDetection]) -> list[FRNRefinedDetection]:
    """Class-aware 2D TF NMS for one Recording.

    Groups are processed in ascending ``class_id``; each group is greedily
    NMS'd; kept detections are concatenated and returned by a stable global
    score-descending sort.
    """
    dets = list(detections)
    if not dets:
        return []
    by_class: dict[int, list[FRNRefinedDetection]] = {}
    for det in dets:
        by_class.setdefault(det.class_id, []).append(det)
    kept: list[FRNRefinedDetection] = []
    for class_id in sorted(by_class):
        kept.extend(_group_physical_class_nms(by_class[class_id]))
    return sorted(kept, key=lambda item: item.confidence, reverse=True)


def postprocess_detections(
    detections: Sequence[FRNRefinedDetection],
) -> list[FRNRefinedDetection]:
    """Frozen full postprocess: threshold then class-aware NMS."""
    survivors = [d for d in detections if d.confidence >= _SCORE_THRESHOLD]
    return physical_class_nms(survivors)
