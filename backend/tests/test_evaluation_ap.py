import pytest

from app.evaluation.ap import (
    AveragePrecisionResult,
    EvaluationGroundTruth,
    EvaluationPrediction,
    average_precision_at_iou,
    class_aware_ap_summary,
    localization_ap_summary,
)


def _gt(recording_id, manifest_order, t0, t1, f0, f1, class_id, class_name="Signal"):
    return EvaluationGroundTruth(
        recording_id=recording_id, manifest_order=manifest_order,
        t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1, class_id=class_id, class_name=class_name,
    )


def _pred(recording_id, manifest_order, t0, t1, f0, f1, class_id, confidence, class_name="Signal"):
    return EvaluationPrediction(
        recording_id=recording_id, manifest_order=manifest_order,
        t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1, class_id=class_id, class_name=class_name,
        confidence=confidence,
    )


# A box covering [0,1]x[0,1] GHz-ish physical units; IoU of exact duplicates is 1.0.
BOX = (0.0, 1.0, 0.0, 1_000_000.0)
BOX2 = (0.0, 1.0, 0.0, 500_000.0)  # half width -> IoU 0.5 vs BOX
BOX3 = (0.0, 1.0, 0.0, 490_000.0)  # -> IoU < 0.5 vs BOX


def test_perfect_detections_ap_is_one():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, *BOX, 9, 0.9)]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert result.ap == 1.0
    assert result.gt_count == 1
    assert result.prediction_count == 1


def test_all_false_positives_ap_is_zero():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, 5.0, 6.0, 5_000_000.0, 6_000_000.0, 9, 0.9)]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert result.ap == 0.0


def test_duplicate_prediction_one_tp_then_fp():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [
        _pred("r1", 0, *BOX, 9, 0.9),
        _pred("r1", 0, *BOX, 9, 0.8),
    ]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    # TP then FP: recalls 1/1 -> AP = 1/1 = 1.0
    assert result.ap == 1.0


def test_wrong_class_perfect_bbox_localization_tp_class_ap_zero():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, *BOX, 13, 0.9)]
    loc = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert loc.ap == 1.0
    cls = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=9)
    assert cls.ap == 0.0


def test_cross_recording_overlap_never_matches():
    # A prediction in recording r3 overlaps r1's box perfectly but must never
    # match r1's GT because the recordings differ.
    gts = [_gt("r1", 0, *BOX, 9), _gt("r2", 1, *BOX, 9)]
    preds = [_pred("r3", 2, *BOX, 9, 0.9)]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert result.gt_count == 2
    assert result.prediction_count == 1
    assert result.ap == 0.0


def test_same_confidence_deterministic_tie_order():
    # Two identical-box predictions with the same confidence; one TP, one FP, order stable.
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [
        _pred("r1", 0, *BOX, 9, 0.5),
        _pred("r1", 0, *BOX, 9, 0.5),
    ]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert result.ap == 1.0


def test_class_with_zero_gt_ap_is_none():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, *BOX, 6, 0.9)]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=6)
    assert result.gt_count == 0
    assert result.prediction_count == 1
    assert result.ap is None


def test_gt_exists_no_predictions_ap_is_zero():
    gts = [_gt("r1", 0, *BOX, 9)]
    result = average_precision_at_iou(gts, [], iou_threshold=0.5, class_id=None)
    assert result.ap == 0.0


def test_iou_exactly_050_matches():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, *BOX2, 9, 0.9)]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert result.ap == 1.0


def test_iou_049_does_not_match():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, *BOX3, 9, 0.9)]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    assert result.ap == 0.0


def test_ap50_95_uses_exactly_ten_thresholds():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [_pred("r1", 0, *BOX, 9, 0.9)]
    summary = localization_ap_summary(gts, preds)
    assert len(summary.per_threshold) == 10
    assert [t for t, _ in summary.per_threshold] == [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    assert summary.ap50 == 1.0
    assert summary.ap50_95 == 1.0


def test_101_point_interpolation_hand_fixture():
    # Two GT, ranked predictions TP, FP, TP.
    gts = [
        _gt("r1", 0, *BOX, 9),
        _gt("r1", 0, 2.0, 3.0, 0.0, 1_000_000.0, 9),
    ]
    preds = [
        _pred("r1", 0, *BOX, 9, 0.9),
        _pred("r1", 0, 5.0, 6.0, 5_000_000.0, 6_000_000.0, 9, 0.8),
        _pred("r1", 0, 2.0, 3.0, 0.0, 1_000_000.0, 9, 0.7),
    ]
    result = average_precision_at_iou(gts, preds, iou_threshold=0.5, class_id=None)
    expected = (51 * 1.0 + 50 * (2.0 / 3.0)) / 101.0
    assert result.ap == pytest.approx(expected)


def test_class_aware_summary_zero_gt_class_excluded_from_map():
    gts = [_gt("r1", 0, *BOX, 9)]
    preds = [
        _pred("r1", 0, *BOX, 9, 0.9),
        _pred("r1", 0, *BOX, 6, 0.9),  # class 6 zero-GT -> AP None, excluded from mAP
    ]
    summary = class_aware_ap_summary(gts, preds)
    by_class = {item.class_id: item for item in summary.per_class}
    assert by_class[6].gt_count == 0
    assert by_class[6].prediction_count == 1
    assert by_class[6].ap50 is None
    assert summary.map50 == 1.0  # macro over class 9 only
    assert summary.map50_95 == 1.0


def test_class_aware_summary_macro_mean():
    gts = [
        _gt("r1", 0, *BOX, 9),
        _gt("r1", 0, 2.0, 3.0, 0.0, 1_000_000.0, 6),
    ]
    preds = [
        _pred("r1", 0, *BOX, 9, 0.9),
        _pred("r1", 0, 2.0, 3.0, 0.0, 1_000_000.0, 6, 0.8),
    ]
    summary = class_aware_ap_summary(gts, preds)
    assert summary.map50 == pytest.approx(1.0)
    assert len(summary.per_class) == 2