"""M9.1 Task 12E — frozen full pipeline composition tests.

Level 2: production ``_refine_from_proposals`` fed the frozen CPN proposals
must reproduce the exact same-sample rows of the frozen final detection oracle
for the 8 acceptance recordings (count, order, class_id, confidence/score,
t_start/t_end/f_low/f_high).

Level 3: true live ``run()`` on sample 0 (raw IQ -> LS-STFT -> live CPN batch=1
-> AHLP -> FRN -> postprocess) is a diagnostic; numerical drift from the
historical CPN batch=16 oracle is expected and not a failure.

The constructor contract and parameter-rejection are pure tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PKG = "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3"


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except Exception:
        return False


def _cuda() -> bool:
    if not _has_torch():
        return False
    import torch

    return bool(torch.cuda.is_available())


@pytest.fixture()
def acceptance():
    required = [
        "WSP_TASK12E_FRN_CHECKPOINT",
        "WSP_TASK12E_DETECTOR_CHECKPOINT",
        "WSP_TASK12E_CPN_ORACLE",
        "WSP_TASK12E_RAW_TEST_ROOT",
        "WSP_TASK12E_FINAL_ORACLE",
        "WSP_TASK12E_NORMALIZATION_JSON",
        "WSP_TASK12E_LABEL_SPACE_ROOT",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"missing acceptance env: {missing}")
    if not _cuda():
        pytest.skip("no CUDA")
    return {k: Path(os.environ[k]) for k in required}


def _read_frozen_proposals(sample_id: str, cpn_oracle: Path) -> list[dict]:
    out = []
    for line in cpn_oracle.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if str(r.get("sample_id")) == str(sample_id):
            out.append(r)
    return out


def _read_frozen_final(sample_id: str, final_oracle: Path) -> list[dict]:
    out = []
    for line in final_oracle.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if str(r.get("sample_id")) == str(sample_id):
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Constructor / definition contract (pure)
# ---------------------------------------------------------------------------


def test_task12e_definition_and_constructor_contract(tmp_path):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import (
        ZoomSpecFrozenPipeline,
    )
    from app.pipelines.base import PipelineDefinition
    from app.labels.service import LabelSpace, LabelClass

    label_space = LabelSpace(id="spacenet_14", version=1, classes=tuple(
        LabelClass(id=i, name=f"class_{i}") for i in range(14)
    ))
    normalization = _norm()
    pipe = ZoomSpecFrozenPipeline.__new__(ZoomSpecFrozenPipeline)
    d = pipe.definition
    assert isinstance(d, PipelineDefinition)
    assert d.id == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert d.version == "1.0.0"
    assert d.label_space == "spacenet_14"
    assert d.task_capability == "detection_classification"
    assert d.executors_supported == ("remote_gpu",)
    assert d.recommended_executor == "remote_gpu"


def _norm():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
        LSSTFTNormalization,
    )

    return LSSTFTNormalization(
        percentile_low=1.0, percentile_high=99.5,
        value_low=0.1356358379125595, value_high=6.512740135192871,
    )


def test_task12e_rejects_non_empty_parameters():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import (
        ZoomSpecFrozenPipeline,
    )

    pipe = ZoomSpecFrozenPipeline.__new__(ZoomSpecFrozenPipeline)
    with pytest.raises(ValueError, match="parameters"):
        pipe.run(None, {"threshold": 0.5}, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Metadata corrective — distinct stage counts (CPU / no checkpoint)
# ---------------------------------------------------------------------------


def _fake_refine_batch(results):
    def _refine(self, candidates, **kw):
        return list(results)
    return _refine


def test_task12e_metadata_stage_counts_distinct(monkeypatch):
    """4 proposals -> FRN valid 3 -> threshold survivors 2 -> post-NMS 1.

    Uses fakes/monkeypatches; no checkpoint/GPU required. Proves the internal
    composition stats and the run() run_metadata expose distinct counts.
    """
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3 import pipeline as pipe_mod
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import (
        ZoomSpecFrozenPipeline,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNPrediction,
        FRNRefinedDetection,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal
    from app.pipelines.base import RecordingInput
    from app.labels.service import LabelSpace, LabelClass

    def _det(idx, *, conf, class_id=9, t0=None):
        prop = CPNProposal(
            t_start_s=0.0, t_end_s=1.0, f_low_hz=2400e6, f_high_hz=2402e6,
            bandwidth_tier=1, confidence=conf,
        )
        pred = FRNPrediction(class_id, 1.0, 1.0, 0.5, 0.5, 0.5, 0.0)
        return FRNRefinedDetection(
            proposal=prop, prediction=pred, class_id=class_id,
            t_start_s=t0 if t0 is not None else idx * 10.0,
            t_end_s=(t0 if t0 is not None else idx * 10.0) + 1.0,
            f_low_hz=2400e6, f_high_hz=2402e6, confidence=conf,
        )

    # 4 candidates: [None, conf 0.0005 (below thresh), conf 0.0015, conf 0.5]
    #   FRN valid = 3 (non-None)
    #   threshold survivors (>=0.001) = {0.0015, 0.5} = 2
    #   NMS: 0.5 and 0.0015 same class, overlapping -> keeps 0.5 -> post-NMS 1
    fake_refine = [
        None,
        _det(1, conf=0.0005),
        _det(2, conf=0.0015, t0=0.0),
        _det(3, conf=0.5, t0=0.0),
    ]

    pipe = ZoomSpecFrozenPipeline.__new__(ZoomSpecFrozenPipeline)
    pipe._name_by_id = {i: f"class_{i}" for i in range(14)}
    pipe._device = 0
    pipe._frn = type("FakeFRN", (), {"refine_batch": _fake_refine_batch(fake_refine)})()
    pipe._detector = type("FakeDet", (), {"detect_batch": lambda self, specs, batch_size=16: [[_proposal(i) for i in range(4)]]})()
    pipe._label_space = LabelSpace(id="spacenet_14", version=1, classes=tuple(
        LabelClass(id=i, name=f"class_{i}") for i in range(14)
    ))
    pipe._normalization = None

    recording = RecordingInput(
        id="x", data_path=Path("/tmp/x.bin"), data_format="float16_interleaved_le",
        sample_rate_hz=100e6, center_frequency_hz=2.4e9,
        frequency_low_hz=2400e6, frequency_high_hz=2410e6,
        duration_s=0.05, label_space="spacenet_14",
    )

    # Fake LS-STFT + reader so run() stays CPU/no-GPU.
    monkeypatch.setattr(pipe_mod, "read_segment_from_path", lambda *a, **k: np.zeros(1000, dtype=np.complex64))
    monkeypatch.setattr(pipe_mod, "build_ls_stft_spectrogram", lambda *a, **k: None)
    monkeypatch.setattr(pipe_mod, "purify_candidate", lambda iq, **kw: object())

    # 1) internal composition stats
    comp = pipe._compose_from_proposals(np.zeros(1000, dtype=np.complex64), recording, [_proposal(i) for i in range(4)])
    assert comp.frn_valid_count == 3
    assert comp.score_threshold_survivor_count == 2
    assert comp.post_nms_count == 1
    assert len(comp.payloads) == 1

    # 2) run_metadata exposes 4 / 3 / 2 / 1
    out = pipe.run(recording, {}, Path("/tmp/ws"))
    md = out.run_metadata
    assert md["cpn_proposal_count"] == 4
    assert md["frn_valid_count"] == 3
    assert md["score_threshold_survivor_count"] == 2
    assert md["post_nms_count"] == 1
    assert len(out.detections) == 1


def _proposal(i: int):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

    return CPNProposal(
        t_start_s=i * 10.0, t_end_s=i * 10.0 + 1.0,
        f_low_hz=2400e6, f_high_hz=2402e6, bandwidth_tier=1, confidence=0.5,
    )


# ---------------------------------------------------------------------------
# Level 2 — exact scientific composition from frozen CPN proposals
# ---------------------------------------------------------------------------


def test_task12e_level2_frozen_oracle_parity(acceptance):
    """For each acceptance recording, production _refine_from_proposals on the
    frozen CPN proposals must reproduce the frozen final oracle exactly."""
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import (
        ZoomSpecFrozenPipeline,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
        build_ls_stft_spectrogram,
        LSSTFTNormalization,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import (
        CPNDetector,
        CPNProposal,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner
    from app.pipelines.base import RecordingInput
    from app.labels.service import LabelSpaceService

    norm_json = json.loads(acceptance["WSP_TASK12E_NORMALIZATION_JSON"].read_text())
    normalization = LSSTFTNormalization(
        percentile_low=float(norm_json["percentile_low"]),
        percentile_high=float(norm_json["percentile_high"]),
        value_low=float(norm_json["value_low"]),
        value_high=float(norm_json["value_high"]),
    )
    labels = LabelSpaceService(acceptance["WSP_TASK12E_LABEL_SPACE_ROOT"]).get("spacenet_14")

    pipe = ZoomSpecFrozenPipeline(
        detector_checkpoint_path=acceptance["WSP_TASK12E_DETECTOR_CHECKPOINT"],
        frn_checkpoint_path=acceptance["WSP_TASK12E_FRN_CHECKPOINT"],
        normalization=normalization,
        label_space=labels,
        device=0,
    )

    recordings = ["0", "1", "6", "10", "22", "151", "1127", "2294"]
    all_exact = True
    failures = []
    for sid in recordings:
        frozen_props = _read_frozen_proposals(sid, acceptance["WSP_TASK12E_CPN_ORACLE"])
        # Whole IQ + recording metadata
        bin_path = acceptance["WSP_TASK12E_RAW_TEST_ROOT"] / f"{sid}.bin"
        meta = json.loads((bin_path.with_suffix(".json")).read_text())
        lo_mhz, hi_mhz = meta["observation_range"]
        fs_hz = (hi_mhz - lo_mhz) * 1e6
        import app.recordings.reader as reader

        iq = reader.read_segment_from_path(bin_path, "float16_interleaved_le")
        recording = RecordingInput(
            id=sid,
            data_path=bin_path,
            data_format="float16_interleaved_le",
            sample_rate_hz=fs_hz,
            center_frequency_hz=((lo_mhz + hi_mhz) / 2.0) * 1e6,
            frequency_low_hz=lo_mhz * 1e6,
            frequency_high_hz=hi_mhz * 1e6,
            duration_s=iq.size / fs_hz,
            label_space="spacenet_14",
        )
        proposals = [
            CPNProposal(
                t_start_s=r["t0_s"], t_end_s=r["t1_s"],
                f_low_hz=r["f0_hz"], f_high_hz=r["f1_hz"],
                bandwidth_tier=r["bandwidth_tier"], confidence=r["score"],
            )
            for r in frozen_props
        ]
        payloads = pipe._refine_from_proposals(iq, recording, proposals)

        oracle = _read_frozen_final(sid, acceptance["WSP_TASK12E_FINAL_ORACLE"])
        name_by_id = {c.id: c.name for c in labels.classes}
        ok = (
            len(payloads) == len(oracle)
            and all(p.class_id == int(o["class_id"]) for p, o in zip(payloads, oracle))
            and all(p.confidence == float(o["score"]) for p, o in zip(payloads, oracle))
            and all(p.t_start_s == float(o["t0_s"]) for p, o in zip(payloads, oracle))
            and all(p.t_end_s == float(o["t1_s"]) for p, o in zip(payloads, oracle))
            and all(p.f_low_hz == float(o["f0_hz"]) for p, o in zip(payloads, oracle))
            and all(p.f_high_hz == float(o["f1_hz"]) for p, o in zip(payloads, oracle))
            and all(p.class_name == name_by_id[p.class_id] for p in payloads)
        )
        if not ok:
            all_exact = False
            failures.append(sid)
    assert all_exact, f"level2 exact parity failed for recordings: {failures}"


# ---------------------------------------------------------------------------
# Level 3 — true live full-chain diagnostic (sample 0)
# ---------------------------------------------------------------------------


def test_task12e_level3_live_sample0_diagnostic(acceptance):
    """Live run() on sample 0 must not crash / must be finite; CPN batch=1
    numerical drift from the frozen batch=16 oracle is a diagnostic, not a
    failure."""
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import (
        ZoomSpecFrozenPipeline,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
        LSSTFTNormalization,
    )
    from app.pipelines.base import RecordingInput
    from app.labels.service import LabelSpaceService

    norm_json = json.loads(acceptance["WSP_TASK12E_NORMALIZATION_JSON"].read_text())
    normalization = LSSTFTNormalization(
        percentile_low=float(norm_json["percentile_low"]),
        percentile_high=float(norm_json["percentile_high"]),
        value_low=float(norm_json["value_low"]),
        value_high=float(norm_json["value_high"]),
    )
    labels = LabelSpaceService(acceptance["WSP_TASK12E_LABEL_SPACE_ROOT"]).get("spacenet_14")

    pipe = ZoomSpecFrozenPipeline(
        detector_checkpoint_path=acceptance["WSP_TASK12E_DETECTOR_CHECKPOINT"],
        frn_checkpoint_path=acceptance["WSP_TASK12E_FRN_CHECKPOINT"],
        normalization=normalization,
        label_space=labels,
        device=0,
    )
    sid = "0"
    bin_path = acceptance["WSP_TASK12E_RAW_TEST_ROOT"] / f"{sid}.bin"
    meta = json.loads((bin_path.with_suffix(".json")).read_text())
    lo_mhz, hi_mhz = meta["observation_range"]
    fs_hz = (hi_mhz - lo_mhz) * 1e6
    import app.recordings.reader as reader

    iq = reader.read_segment_from_path(bin_path, "float16_interleaved_le")
    recording = RecordingInput(
        id=sid,
        data_path=bin_path,
        data_format="float16_interleaved_le",
        sample_rate_hz=fs_hz,
        center_frequency_hz=((lo_mhz + hi_mhz) / 2.0) * 1e6,
        frequency_low_hz=lo_mhz * 1e6,
        frequency_high_hz=hi_mhz * 1e6,
        duration_s=iq.size / fs_hz,
        label_space="spacenet_14",
    )
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out = pipe.run(recording, {}, Path(td))
    assert out.detections is not None
    assert all(np.isfinite(d.confidence) for d in out.detections)
    assert all(d.t_start_s < d.t_end_s and d.f_low_hz < d.f_high_hz for d in out.detections)
    assert out.run_metadata["kind"] == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert out.run_metadata["post_nms_count"] == len(out.detections)

    # ---- Deterministic live-vs-frozen drift diagnostic ----
    frozen = _read_frozen_final(sid, acceptance["WSP_TASK12E_FINAL_ORACLE"])

    def _box_dict(d, *, pref=""):
        return {
            "t_start_s": d.t_start_s, "t_end_s": d.t_end_s,
            "f_low_hz": d.f_low_hz, "f_high_hz": d.f_high_hz,
        }

    def _oracle_dict(o):
        return {"t_start_s": float(o["t0_s"]), "t_end_s": float(o["t1_s"]),
                "f_low_hz": float(o["f0_hz"]), "f_high_hz": float(o["f1_hz"])}

    from app.evaluation.matching import bbox_iou

    # Deterministic greedy one-to-one matching by best physical IoU.
    used_live = [False] * len(out.detections)
    used_frozen = [False] * len(frozen)
    matched = []
    for li, ldet in enumerate(out.detections):
        best_i = -1
        best_iou = 0.0
        for fi, forac in enumerate(frozen):
            if used_frozen[fi]:
                continue
            iou = bbox_iou(_box_dict(ldet), _oracle_dict(forac))
            if iou > best_iou:
                best_iou = iou
                best_i = fi
        if best_i >= 0 and best_iou >= 0.5:
            used_live[li] = True
            used_frozen[best_i] = True
            matched.append((li, best_i, best_iou))

    live_cpn = out.run_metadata["cpn_proposal_count"]
    matched_count = len(matched)
    live_unmatched = sum(1 for u in used_live if not u)
    frozen_unmatched = sum(1 for u in used_frozen if not u)

    dt0 = [abs(out.detections[li].t_start_s - float(frozen[fi]["t0_s"])) for li, fi, _ in matched] or [0.0]
    dt1 = [abs(out.detections[li].t_end_s - float(frozen[fi]["t1_s"])) for li, fi, _ in matched] or [0.0]
    df0 = [abs(out.detections[li].f_low_hz - float(frozen[fi]["f0_hz"])) for li, fi, _ in matched] or [0.0]
    df1 = [abs(out.detections[li].f_high_hz - float(frozen[fi]["f1_hz"])) for li, fi, _ in matched] or [0.0]
    dc = [abs(out.detections[li].confidence - float(frozen[fi]["score"])) for li, fi, _ in matched] or [0.0]
    class_change = sum(
        1 for li, fi, _ in matched if out.detections[li].class_id != int(frozen[fi]["class_id"])
    )

    report = {
        "live_cpn_proposal_count": live_cpn,
        "live_final_detection_count": len(out.detections),
        "frozen_final_detection_count": len(frozen),
        "matched_count": matched_count,
        "live_unmatched_count": live_unmatched,
        "frozen_unmatched_count": frozen_unmatched,
        "max_t_start_drift": max(dt0),
        "max_t_end_drift": max(dt1),
        "max_f_low_drift": max(df0),
        "max_f_high_drift": max(df1),
        "max_confidence_drift": max(dc),
        "class_change_count": class_change,
    }
    # Record for the provenance report; no tolerance acceptance here.
    print("TASK12E_LEVEL3_DIAGNOSTIC", json.dumps(report))
    # Consistency invariants only.
    assert live_cpn > 0
    assert matched_count + live_unmatched == len(out.detections)
    assert matched_count + frozen_unmatched == len(frozen)
    assert all(0 <= d.confidence <= 1 for d in out.detections)
