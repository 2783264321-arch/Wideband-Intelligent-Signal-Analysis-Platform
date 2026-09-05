import pytest

from app.evaluation.matching import MatchPair, MatchResult, bbox_iou, match_predictions
from app.evaluation.metrics import DetectionMetrics, calculate_detection_metrics


def _box(t0, t1, f0, f1):
    return {"t_start_s": t0, "t_end_s": t1, "f_low_hz": f0, "f_high_hz": f1}


def test_exact_overlap_iou_is_one():
    a = _box(0.0, 1.0, 100.0, 200.0)
    assert bbox_iou(a, a) == 1.0


def test_disjoint_boxes_iou_is_zero():
    a = _box(0.0, 1.0, 100.0, 200.0)
    b = _box(2.0, 3.0, 300.0, 400.0)
    assert bbox_iou(a, b) == 0.0


def test_partial_overlap_uses_physical_area():
    a = _box(0.0, 2.0, 0.0, 2.0)   # area 4
    b = _box(1.0, 3.0, 1.0, 3.0)   # area 4
    # intersection: t=[1,2] f=[1,2] -> area 1; union 4+4-1=7
    assert bbox_iou(a, b) == pytest.approx(1.0 / 7.0)


def test_single_valid_pair_matches():
    gt = [_box(0.0, 1.0, 0.0, 1.0)]
    pred = [_box(0.0, 1.0, 0.0, 1.0)]
    result = match_predictions(gt, pred)
    assert [(p.gt_index, p.pred_index, p.iou) for p in result.pairs] == [(0, 0, 1.0)]
    assert result.unmatched_gt_indices == ()
    assert result.unmatched_pred_indices == ()


def test_one_prediction_matches_only_one_gt():
    gt = [_box(0.0, 1.0, 0.0, 1.0), _box(0.0, 1.0, 0.0, 1.0)]
    pred = [_box(0.0, 1.0, 0.0, 1.0)]
    result = match_predictions(gt, pred)
    assert len(result.pairs) == 1
    assert len(result.unmatched_gt_indices) == 1
    assert result.unmatched_pred_indices == ()


def test_below_threshold_prediction_unmatched():
    gt = [_box(0.0, 1.0, 0.0, 1.0)]
    pred = [_box(0.0, 1.0, 0.0, 0.4)]  # IoU 0.4 < 0.5
    result = match_predictions(gt, pred)
    assert result.pairs == ()
    assert result.unmatched_pred_indices == (0,)
    assert result.unmatched_gt_indices == (0,)


def test_exactly_threshold_matches():
    gt = [_box(0.0, 1.0, 0.0, 1.0)]
    pred = [_box(0.0, 1.0, 0.0, 0.5)]  # IoU 0.5 exactly
    result = match_predictions(gt, pred, iou_threshold=0.5)
    assert len(result.pairs) == 1
    assert result.pairs[0].iou == pytest.approx(0.5)


def test_unmatched_prediction_and_gt_candidates():
    gt = [_box(0.0, 1.0, 0.0, 1.0), _box(10.0, 11.0, 0.0, 1.0)]
    pred = [_box(0.0, 1.0, 0.0, 1.0), _box(100.0, 101.0, 0.0, 1.0)]
    result = match_predictions(gt, pred)
    assert len(result.pairs) == 1
    assert result.unmatched_gt_indices == (1,)
    assert result.unmatched_pred_indices == (1,)

def test_metrics_known_example():
    result = MatchResult(
        pairs=(MatchPair(0, 0, 0.6), MatchPair(1, 1, 0.7)),
        unmatched_gt_indices=(2,),
        unmatched_pred_indices=(3,),
    )
    metrics = calculate_detection_metrics(result, gt_count=3, prediction_count=4)
    assert metrics.tp == 2
    assert metrics.fp == 2  # 4 predictions - 2 matched
    assert metrics.fn == 1  # 3 GT - 2 matched
    assert metrics.precision == pytest.approx(2 / 4)
    assert metrics.recall == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(2 * (2 / 4) * (2 / 3) / ((2 / 4) + (2 / 3)))
    assert metrics.mean_matched_iou == pytest.approx(0.65)


def test_metrics_zero_denominators_return_zero():
    result = MatchResult(pairs=(), unmatched_gt_indices=(0,), unmatched_pred_indices=())
    metrics = calculate_detection_metrics(result, gt_count=1, prediction_count=0)
    assert metrics.tp == 0
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0
    assert metrics.mean_matched_iou is None


def test_metrics_no_matched_pairs_mean_iou_none():
    result = MatchResult(pairs=(), unmatched_gt_indices=(0,), unmatched_pred_indices=(0,))
    metrics = calculate_detection_metrics(result, gt_count=1, prediction_count=1)
    assert metrics.mean_matched_iou is None
