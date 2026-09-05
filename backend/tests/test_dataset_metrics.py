import pytest

from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.dataset_metrics import (
    DatasetDiagnostics,
    EvaluationSample,
    compute_dataset_diagnostics,
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


def _box(t0, t1, f0, f1):
    return (t0, t1, f0, f1)


def test_two_recordings_aggregate_localization_counts():
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(_gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9),),
            predictions=(_pred("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9, 0.9),),
        ),
        EvaluationSample(
            recording_id="r2", manifest_order=1,
            ground_truths=(_gt("r2", 1, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9),),
            predictions=(),  # missed GT
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=True)
    assert diag.localization.tp == 1
    assert diag.localization.fp == 0
    assert diag.localization.fn == 1
    assert diag.localization.precision == 1.0
    assert diag.localization.recall == 0.5


def test_wrong_class_localized_pair_counts():
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(_gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9, "LoRa 250kHz"),),
            predictions=(_pred("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 13, 0.9, "FM"),),
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=True)
    assert diag.localization.tp == 1  # localization match still TP
    assert diag.classification.class_wrong == 1
    assert diag.classification.matched_accuracy == 0.0
    assert len(diag.classification.confusions) == 1
    # class-aware: pair wrong -> 1 FP to pred class 13, 1 FN to GT class 9
    by_class = {item.class_id: item for item in diag.per_class}
    assert by_class[13].fp == 1
    assert by_class[9].fn == 1


def test_unmatched_prediction_and_gt_per_class():
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(_gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9),),
            predictions=(_pred("r1", 0, *_box(5.0, 6.0, 5_000_000.0, 6_000_000.0), 6, 0.9),),
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=True)
    assert diag.localization.tp == 0
    assert diag.localization.fp == 1
    assert diag.localization.fn == 1
    by_class = {item.class_id: item for item in diag.per_class}
    assert by_class[6].fp == 1  # unmatched prediction -> FP to pred class
    assert by_class[9].fn == 1  # unmatched GT -> FN to GT class


def test_matched_count_zero_accuracy_none():
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(_gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9),),
            predictions=(_pred("r1", 0, *_box(5.0, 6.0, 5_000_000.0, 6_000_000.0), 9, 0.9),),
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=True)
    assert diag.classification.matched_count == 0
    assert diag.classification.matched_accuracy is None


def test_detection_only_classification_results_none():
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(_gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 0, "Signal"),),
            predictions=(_pred("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 0, 0.9, "Signal"),),
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=False)
    assert diag.localization.tp == 1
    assert diag.classification is None
    assert diag.class_aware is None
    assert diag.per_class == ()


def test_confusion_pairs_aggregate_and_sort():
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(
                _gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 13, "FM"),
                _gt("r1", 0, *_box(2.0, 3.0, 0.0, 1_000_000.0), 9, "LoRa 250kHz"),
            ),
            predictions=(
                _pred("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9, 0.9, "LoRa 250kHz"),
                _pred("r1", 0, *_box(2.0, 3.0, 0.0, 1_000_000.0), 6, 0.8, "BLE LE1M"),
            ),
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=True)
    confusions = list(diag.classification.confusions)
    assert [(c.gt_class_id, c.pred_class_id) for c in confusions] == [(9, 6), (13, 9)]
    assert confusions[0].gt_class_id == 9
    assert confusions[1].gt_class_id == 13


def test_cross_recording_boxes_evaluated_separately():
    # Two recordings with identical box coordinates; they must never cross-match.
    samples = [
        EvaluationSample(
            recording_id="r1", manifest_order=0,
            ground_truths=(_gt("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9),),
            predictions=(_pred("r1", 0, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9, 0.9),),
        ),
        EvaluationSample(
            recording_id="r2", manifest_order=1,
            ground_truths=(_gt("r2", 1, *_box(0.0, 1.0, 0.0, 1_000_000.0), 9),),
            predictions=(),  # r2 has a GT but no prediction; the r1 prediction must not match it
        ),
    ]
    diag = compute_dataset_diagnostics(samples, classification_applicable=True)
    assert diag.localization.tp == 1
    assert diag.localization.fp == 0
    assert diag.localization.fn == 1