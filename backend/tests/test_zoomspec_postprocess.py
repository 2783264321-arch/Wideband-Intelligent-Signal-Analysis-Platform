"""M9.1 Task 12E — frozen postprocess (threshold + class-aware TF NMS) tests.

Pure tests run in the repo ``.venv`` (no Torch). Level-1 parity compares the
production postprocess against the actual historical ``tf_iou`` /
``physical_class_nms`` using test-only legacy imports and frozen diagnostic
rows, guarded by the acceptance environment.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PKG = "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3"

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (  # noqa: E402
    FRNPrediction,
    FRNRefinedDetection,
)
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import PurifiedCandidate  # noqa: E402
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal  # noqa: E402


def _proposal(i: int, *, t0=0.0, t1=1.0, f0=2400.0e6, f1=2402.0e6, conf=0.5) -> CPNProposal:
    return CPNProposal(
        t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1,
        bandwidth_tier=1, confidence=conf,
    )


def _pred(class_id: int, *, signal=1.0, cls=1.0) -> FRNPrediction:
    return FRNPrediction(
        class_id=class_id,
        signal_probability=signal,
        class_probability=cls,
        start_norm=0.5,
        duration_norm=0.5,
        bandwidth_norm=0.5,
        center_offset_norm=0.0,
    )


def _det(i: int, *, class_id=0, t0=0.0, t1=1.0, f0=2400.0e6, f1=2402.0e6, conf=0.5) -> FRNRefinedDetection:
    prop = _proposal(i, t0=t0, t1=t1, f0=f0, f1=f1, conf=conf)
    pred = _pred(class_id, signal=conf, cls=1.0)
    return FRNRefinedDetection(
        proposal=prop, prediction=pred, class_id=class_id,
        t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1, confidence=conf,
    )


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# tf_iou
# ---------------------------------------------------------------------------


def test_task12e_iou_identical_boxes():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    a = _det(0)
    b = _det(1)
    assert tf_iou(a, b) == pytest.approx(1.0)


def test_task12e_iou_disjoint():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    a = _det(0, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6)
    b = _det(1, t0=2.0, t1=3.0, f0=2400e6, f1=2402e6)
    assert tf_iou(a, b) == 0.0


def test_task12e_iou_touching_time_edge():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    a = _det(0, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6)
    b = _det(1, t0=1.0, t1=2.0, f0=2400e6, f1=2402e6)
    assert tf_iou(a, b) == 0.0


def test_task12e_iou_touching_frequency_edge():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    a = _det(0, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6)
    b = _det(1, t0=0.0, t1=1.0, f0=2402e6, f1=2404e6)
    assert tf_iou(a, b) == 0.0


def test_task12e_iou_partial_overlap():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    # a: time [0,2]s x freq [2400,2404] MHz -> area 2*4e6
    a = _det(0, t0=0.0, t1=2.0, f0=2400e6, f1=2404e6)
    # b: time [1,3]s x freq [2402,2406] MHz -> area 2*4e6
    b = _det(1, t0=1.0, t1=3.0, f0=2402e6, f1=2406e6)
    # intersection: time [1,2]=1s, freq [2402,2404]=2e6 -> 2e6
    inter = 1.0 * 2.0e6
    union = 2.0 * 4.0e6 + 2.0 * 4.0e6 - inter
    assert tf_iou(a, b) == pytest.approx(inter / union)


def test_task12e_iou_containment():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    outer = _det(0, t0=0.0, t1=4.0, f0=2400e6, f1=2404e6)  # area 16
    inner = _det(1, t0=1.0, t1=2.0, f0=2401e6, f1=2402e6)  # area 1
    assert tf_iou(outer, inner) == pytest.approx(1.0 / 16.0)


def test_task12e_iou_zero_area_union():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import tf_iou

    # Degenerate zero-area box -> union<=0 -> 0
    a = _det(0, t0=0.0, t1=0.0, f0=2400e6, f1=2402e6)
    b = _det(1, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6)
    assert tf_iou(a, b) == 0.0


# ---------------------------------------------------------------------------
# threshold
# ---------------------------------------------------------------------------


def test_task12e_threshold_below_exact_above():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import (
        postprocess_detections,
        _SCORE_THRESHOLD,
    )

    assert _SCORE_THRESHOLD == 0.001
    # Distinct boxes so NMS does not suppress the boundary-kept detection.
    below = _det(0, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.0005)
    exact = _det(1, t0=5.0, t1=6.0, f0=2400e6, f1=2402e6, conf=0.001)
    above = _det(2, t0=10.0, t1=11.0, f0=2400e6, f1=2402e6, conf=0.002)
    out = postprocess_detections([below, exact, above])
    # below removed; exact and above kept.
    kept_confs = sorted(d.confidence for d in out)
    assert kept_confs == [0.001, 0.002]


# ---------------------------------------------------------------------------
# NMS
# ---------------------------------------------------------------------------


def test_task12e_nms_same_class_high_iou_suppresses():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import postprocess_detections

    high = _det(0, class_id=9, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.9)
    low = _det(1, class_id=9, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.8)  # identical -> IoU 1.0
    out = postprocess_detections([high, low])
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_task12e_nms_same_class_low_iou_survives():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import postprocess_detections

    a = _det(0, class_id=9, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.9)
    b = _det(1, class_id=9, t0=5.0, t1=6.0, f0=2400e6, f1=2402e6, conf=0.8)  # disjoint -> IoU 0
    out = postprocess_detections([a, b])
    assert len(out) == 2


def test_task12e_nms_iou_exactly_threshold_suppresses():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import (
        tf_iou,
        postprocess_detections,
    )

    # Two same-class boxes whose TF IoU is EXACTLY 0.7 (7/10, exactly representable).
    # a: time [0,10], freq [0,1] -> area 10
    # b: time [3,10], freq [0,1] -> area 7, intersection area 7, union 10 -> IoU 0.7
    a = _det(0, class_id=9, t0=0.0, t1=10.0, f0=0.0, f1=1.0, conf=0.9)
    b = _det(1, class_id=9, t0=3.0, t1=10.0, f0=0.0, f1=1.0, conf=0.8)
    iou = tf_iou(a, b)
    assert iou == 0.7
    # Historical predicate: a candidate survives only if tf_iou(best, cand) < 0.7.
    # At IoU == 0.7 it is suppressed.
    out = postprocess_detections([a, b])
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_task12e_nms_different_class_identical_box_both_survive():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import postprocess_detections

    a = _det(0, class_id=9, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.9)
    b = _det(1, class_id=10, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.8)
    out = postprocess_detections([a, b])
    assert len(out) == 2
    classes = sorted(d.class_id for d in out)
    assert classes == [9, 10]


def test_task12e_nms_equal_score_same_class_preserves_input_order():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import postprocess_detections

    a = _det(0, class_id=9, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.5)
    b = _det(1, class_id=9, t0=2.0, t1=3.0, f0=2400e6, f1=2402e6, conf=0.5)  # equal score, disjoint
    out = postprocess_detections([a, b])
    assert len(out) == 2
    # stable sort preserves input order for equal score
    assert out[0].proposal.t_start_s == 0.0
    assert out[1].proposal.t_start_s == 2.0


def test_task12e_nms_empty_and_single():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import postprocess_detections

    assert postprocess_detections([]) == []
    one = postprocess_detections([_det(0, conf=0.5)])
    assert len(one) == 1


def test_task12e_nms_final_global_score_desc():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import postprocess_detections

    a = _det(0, class_id=3, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.3)
    b = _det(1, class_id=9, t0=0.0, t1=1.0, f0=2400e6, f1=2402e6, conf=0.9)
    c = _det(2, class_id=9, t0=5.0, t1=6.0, f0=2400e6, f1=2402e6, conf=0.7)
    out = postprocess_detections([a, b, c])
    scores = [d.confidence for d in out]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Level 1 — historical postprocess parity
# ---------------------------------------------------------------------------


def test_task12e_level1_historical_postprocess_parity(tmp_path):
    """Production postprocess must match the historical helpers exactly."""
    leg = os.environ.get("WSP_TASK12D_LEGACY_ROOT")
    if not leg:
        pytest.skip("WSP_TASK12D_LEGACY_ROOT not set")
    import sys

    sys.path.insert(0, str(Path(leg) / "src"))
    from zoomspec_repro.schema import Detection as HistDetection
    from zoomspec_repro.metrics import tf_iou as hist_tf_iou
    from zoomspec_repro.metrics import physical_class_nms as hist_nms

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import (
        tf_iou,
        physical_class_nms,
    )

    # Random-ish but deterministic set of detections spanning classes/overlaps.
    import numpy as np

    rng = np.random.default_rng(0)
    dets = []
    for i in range(40):
        class_id = int(rng.integers(0, 14))
        t0 = float(rng.uniform(0, 0.1))
        t1 = t0 + float(rng.uniform(0.001, 0.01))
        f0 = 2400.0e6 + float(rng.uniform(0, 30.0e6))
        f1 = f0 + float(rng.uniform(0.1e6, 3.0e6))
        conf = float(rng.uniform(0.0, 1.0))
        dets.append(_det(i, class_id=class_id, t0=t0, t1=t1, f0=f0, f1=f1, conf=conf))

    # tf_iou equivalence on all pairs
    for i in range(len(dets)):
        for j in range(len(dets)):
            a = dets[i]
            b = dets[j]
            ha = HistDetection("0", a.t_start_s, a.t_end_s, a.f_low_hz, a.f_high_hz, a.class_id, a.confidence)
            hb = HistDetection("0", b.t_start_s, b.t_end_s, b.f_low_hz, b.f_high_hz, b.class_id, b.confidence)
            assert tf_iou(a, b) == hist_tf_iou(ha, hb)

    # NMS equivalence
    hist_dets = [
        HistDetection("0", d.t_start_s, d.t_end_s, d.f_low_hz, d.f_high_hz, d.class_id, d.confidence)
        for d in dets
    ]
    prod_out = physical_class_nms(dets)
    hist_out = hist_nms(hist_dets, 0.7)
    assert len(prod_out) == len(hist_out)
    for pd, hd in zip(prod_out, hist_out):
        assert pd.class_id == hd.class_id
        assert pd.confidence == hd.score
        assert pd.t_start_s == hd.t0_s
        assert pd.t_end_s == hd.t1_s
        assert pd.f_low_hz == hd.f0_hz
        assert pd.f_high_hz == hd.f1_hz
