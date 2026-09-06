from dataclasses import replace

import pytest

from app.benchmarks.protocol import (
    PHYSICAL_TF_PROTOCOL_V1,
    PHYSICAL_TF_PROTOCOL_V2,
    build_protocol_view,
    canonicalize_ground_truths,
)
from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.dataset_metrics import EvaluationSample
from app.benchmarks.loader import LoadedBenchmark


def gt(*, recording_id="rec_a", order=0, t0=0.01, t1=0.02,
       f0=2_440_600_000.0, f1=2_440_700_000.0, class_id=9,
       class_name="LoRa 250kHz"):
    return EvaluationGroundTruth(
        recording_id=recording_id,
        manifest_order=order,
        t_start_s=t0,
        t_end_s=t1,
        f_low_hz=f0,
        f_high_hz=f1,
        class_id=class_id,
        class_name=class_name,
    )


def loaded(*gts):
    by_recording = {}
    for item in gts:
        by_recording.setdefault((item.recording_id, item.manifest_order), []).append(item)
    samples = tuple(
        EvaluationSample(
            recording_id=recording_id,
            manifest_order=order,
            ground_truths=tuple(items),
            predictions=(),
        )
        for (recording_id, order), items in sorted(by_recording.items(), key=lambda kv: kv[0][1])
    )
    return LoadedBenchmark(
        samples=samples,
        ground_truths=tuple(gts),
        predictions=(),
        runs_by_recording={},
        recordings_by_id={},
    )


def test_v2_removes_exact_physical_class_duplicate():
    a = gt()
    duplicate = replace(a, class_name="display alias does not affect identity")
    view = build_protocol_view(PHYSICAL_TF_PROTOCOL_V2, loaded(a, duplicate))
    assert len(view.ground_truths) == 1
    assert view.ground_truth_accounting.raw_count == 2
    assert view.ground_truth_accounting.canonical_count == 1
    assert view.ground_truth_accounting.removed_count == 1


def test_v1_keeps_exact_duplicate():
    a = gt()
    view = build_protocol_view(PHYSICAL_TF_PROTOCOL_V1, loaded(a, a))
    assert len(view.ground_truths) == 2
    assert view.ground_truth_accounting.raw_count == 2
    assert view.ground_truth_accounting.canonical_count == 2
    assert view.ground_truth_accounting.removed_count == 0


def test_minuscule_coordinate_change_is_not_duplicate():
    a = gt()
    b = replace(a, t_end_s=a.t_end_s + 1e-15)
    assert len(canonicalize_ground_truths((a, b))) == 2


def test_same_box_different_class_is_not_duplicate():
    a = gt()
    b = replace(a, class_id=8, class_name="Zigbee")
    assert len(canonicalize_ground_truths((a, b))) == 2


def test_same_box_different_recording_is_not_duplicate():
    a = gt(recording_id="rec_a", order=0)
    b = gt(recording_id="rec_b", order=1)
    view = build_protocol_view(PHYSICAL_TF_PROTOCOL_V2, loaded(a, b))
    assert len(view.ground_truths) == 2


def test_canonical_output_order_ignores_input_order_and_uses_class_name_only_as_display_tie_break():
    early = gt(t0=0.01, class_name="aaa")
    late = gt(t0=0.03, class_name="zzz")
    duplicate_a = gt(t0=0.05, class_name="zzz")
    duplicate_b = gt(t0=0.05, class_name="aaa")
    first = canonicalize_ground_truths((late, duplicate_a, early, duplicate_b))
    second = canonicalize_ground_truths((duplicate_b, early, duplicate_a, late))
    assert [(x.t_start_s, x.class_id) for x in first] == [(0.01, 9), (0.03, 9), (0.05, 9)]
    assert first == second
    assert first[-1].class_name == "aaa"


def test_unknown_protocol_is_rejected():
    with pytest.raises(ValueError, match="Unsupported evaluation protocol"):
        build_protocol_view("physical_tf_detection_ap_v999", loaded(gt()))