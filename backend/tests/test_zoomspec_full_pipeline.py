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
