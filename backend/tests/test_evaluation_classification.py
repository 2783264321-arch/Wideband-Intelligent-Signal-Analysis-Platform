import pytest

from app.evaluation.matching import MatchPair, MatchResult, match_predictions
from app.evaluation.metrics import (
    ClassAwareMetrics,
    ClassificationConfusion,
    ClassificationMetrics,
    calculate_class_aware_metrics,
    calculate_classification_metrics,
    calculate_detection_metrics,
)

# GT classes by index: gt0=LoRa(9), gt1=WiFi(3), gt2=BLE(6)
GT_CLASSES = {0: 9, 1: 3, 2: 6}
# Prediction classes by index
PRED_CLASSES = {0: 9, 1: 13, 2: 6, 3: 5}

# Bounding boxes (all overlap so IoU matching is controlled by the MatchResult we construct)
_BOXES = {
    0: {"t_start_s": 0.0, "t_end_s": 1.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
    1: {"t_start_s": 0.0, "t_end_s": 1.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
    2: {"t_start_s": 0.0, "t_end_s": 1.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
    3: {"t_start_s": 0.0, "t_end_s": 1.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
}


def _result(pairs, unmatched_gt=(), unmatched_pred=()):
    return MatchResult(
        pairs=tuple(MatchPair(g, p, 0.8) for g, p in pairs),
        unmatched_gt_indices=unmatched_gt,
        unmatched_pred_indices=unmatched_pred,
    )


def test_correct_class():
    result = _result([(0, 0)])
    metrics = calculate_classification_metrics(result, GT_CLASSES, PRED_CLASSES)
    assert metrics.matched_count == 1
    assert metrics.class_correct == 1
    assert metrics.class_wrong == 0
    assert metrics.matched_accuracy == 1.0
    assert metrics.confusions == ()
    aware = calculate_class_aware_metrics(result, gt_count=1, prediction_count=1, gt_classes=GT_CLASSES, pred_classes=PRED_CLASSES)
    assert aware.tp == 1
    assert aware.fp == 0
    assert aware.fn == 0


def test_wrong_class():
    # gt0 = LoRa(9), pred1 = FM(13) — localization TP but wrong class
    result = _result([(0, 1)])
    metrics = calculate_classification_metrics(result, GT_CLASSES, PRED_CLASSES)
    assert metrics.matched_count == 1
    assert metrics.class_correct == 0
    assert metrics.class_wrong == 1
    assert metrics.matched_accuracy == 0.0
    assert len(metrics.confusions) == 1
    assert metrics.confusions[0].gt_class_id == 9
    assert metrics.confusions[0].pred_class_id == 13
    assert metrics.confusions[0].count == 1
    aware = calculate_class_aware_metrics(result, gt_count=1, prediction_count=1, gt_classes=GT_CLASSES, pred_classes=PRED_CLASSES)
    assert aware.tp == 0
    assert aware.fp == 1
    assert aware.fn == 1


def test_mixed():
    # gt0->pred0 correct (LoRa->LoRa)
    # gt1->pred1 wrong (WiFi->FM)
    # pred3 unmatched (FP), gt2 unmatched (FN)
    result = _result([(0, 0), (1, 1)], unmatched_gt=(2,), unmatched_pred=(2, 3))
    metrics = calculate_classification_metrics(result, GT_CLASSES, PRED_CLASSES)
    assert metrics.matched_count == 2
    assert metrics.class_correct == 1
    assert metrics.class_wrong == 1
    assert metrics.matched_accuracy == 0.5
    assert len(metrics.confusions) == 1
    aware = calculate_class_aware_metrics(result, gt_count=3, prediction_count=4, gt_classes=GT_CLASSES, pred_classes=PRED_CLASSES)
    # class_aware_tp = class_correct = 1
    # class_aware_fp = total_pred - class_correct = 4 - 1 = 3
    # class_aware_fn = total_gt - class_correct = 3 - 1 = 2
    assert aware.tp == 1
    assert aware.fp == 3
    assert aware.fn == 2
    assert aware.precision == pytest.approx(1 / 4)
    assert aware.recall == pytest.approx(1 / 3)


def test_no_localization_matches():
    result = _result([], unmatched_gt=(0, 1, 2), unmatched_pred=(0, 1, 2, 3))
    metrics = calculate_classification_metrics(result, GT_CLASSES, PRED_CLASSES)
    assert metrics.matched_count == 0
    assert metrics.class_correct == 0
    assert metrics.class_wrong == 0
    assert metrics.matched_accuracy is None
    assert metrics.confusions == ()
    aware = calculate_class_aware_metrics(result, gt_count=3, prediction_count=4, gt_classes=GT_CLASSES, pred_classes=PRED_CLASSES)
    assert aware.tp == 0
    assert aware.fp == 4
    assert aware.fn == 3
    assert aware.precision == 0.0
    assert aware.recall == 0.0
    assert aware.f1 == 0.0


def test_confusion_aggregation():
    # two different GT both LoRa(9), two preds both FM(13) — aggregate count=2
    gt2 = {0: 9, 1: 9}
    pred2 = {0: 13, 1: 13}
    result = _result([(0, 0), (1, 1)])
    metrics = calculate_classification_metrics(result, gt2, pred2)
    assert metrics.class_correct == 0
    assert len(metrics.confusions) == 1
    assert metrics.confusions[0].gt_class_id == 9
    assert metrics.confusions[0].pred_class_id == 13
    assert metrics.confusions[0].count == 2


def test_confusion_order_is_stable():
    # ensure multiple distinct confusions sort by (gt_class_id, pred_class_id)
    gt3 = {0: 13, 1: 9, 2: 6}
    pred3 = {0: 9, 1: 6, 2: 13}
    result = _result([(0, 0), (1, 1), (2, 2)])
    metrics = calculate_classification_metrics(result, gt3, pred3)
    assert [c.gt_class_id for c in metrics.confusions] == [6, 9, 13]
    assert [c.pred_class_id for c in metrics.confusions] == [13, 6, 9]


def test_localization_metric_behavior_unchanged():
    # Adding classification layers must not change matching or detection metrics.
    # Use physically distinct boxes: gt0 matches pred0, gt1 matches pred1, pred2 is a far FP.
    gts = [
        {"t_start_s": 0.0, "t_end_s": 1.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
        {"t_start_s": 10.0, "t_end_s": 11.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
        {"t_start_s": 20.0, "t_end_s": 21.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
    ]
    preds = [
        {"t_start_s": 0.0, "t_end_s": 1.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
        {"t_start_s": 10.0, "t_end_s": 11.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
        {"t_start_s": 100.0, "t_end_s": 101.0, "f_low_hz": 0.0, "f_high_hz": 1.0},
    ]
    match = match_predictions(gts, preds, iou_threshold=0.5)
    before = calculate_detection_metrics(match, gt_count=3, prediction_count=3)
    assert before.tp == 2
    assert before.fp == 1
    assert before.fn == 1
    # classification derivation uses the same MatchResult without re-running matching
    calc = calculate_classification_metrics(match, GT_CLASSES, PRED_CLASSES)
    assert calc.matched_count == before.tp
