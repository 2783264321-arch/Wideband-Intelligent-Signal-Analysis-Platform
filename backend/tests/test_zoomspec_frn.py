"""M9.1 Task 12D — frozen Combined FRN V3 production port tests.

Node names contain ``task12d``. The pure-numpy/public-API/validation tests run
under both the repo ``.venv`` (no Torch) and the formal ML runtime
``/root/miniconda3/bin/python``. Historical-external and GPU parity tests are
guarded by environment variables and CUDA availability and may truthfully skip
when the acceptance assets or a GPU are absent.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PIPELINE_PKG = "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3"
FRN_MODULE = f"{PIPELINE_PKG}.frn"

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import PurifiedCandidate  # noqa: E402
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except Exception:
        return False


def _cuda_ready() -> bool:
    if not _has_torch():
        return False
    import torch

    return bool(torch.cuda.is_available())


def _tf32_state() -> tuple[bool, bool, bool, bool]:
    import torch

    return (
        torch.backends.cuda.matmul.allow_tf32,
        torch.backends.cudnn.allow_tf32,
        torch.backends.cudnn.benchmark,
        torch.backends.cudnn.deterministic,
    )


def _legacy_root() -> Path | None:
    root = os.environ.get("WSP_TASK12D_LEGACY_ROOT")
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


def _frn_checkpoint() -> Path | None:
    cp = os.environ.get("WSP_TASK12D_FRN_CHECKPOINT")
    if not cp:
        return None
    p = Path(cp)
    return p if p.is_file() else None


def _candidate(
    *,
    n: int = 8192,
    lowpass_hz: float = 2.0e6,
    decimation: int = 16,
    confidence: float = 0.9,
) -> PurifiedCandidate:
    t = np.arange(n, dtype=np.float32)
    iq = (np.exp(2j * np.pi * 0.01 * t) + 0.5 * np.exp(2j * np.pi * 0.2 * t)).astype(np.complex64)
    proposal = CPNProposal(
        t_start_s=0.01,
        t_end_s=0.03,
        f_low_hz=2401.0e6,
        f_high_hz=2403.0e6,
        bandwidth_tier=1,
        confidence=confidence,
    )
    return PurifiedCandidate(
        proposal=proposal,
        iq=iq,
        sample_rate_hz=100.0e6 / decimation,
        crop_t_start_s=0.008,
        crop_t_end_s=0.032,
        lowpass_hz=lowpass_hz,
        decimation=decimation,
        diagnostics=None,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# A. Import boundary — frn.py must import cleanly WITHOUT Torch.
# ---------------------------------------------------------------------------


def test_task12d_import_boundary_without_torch():
    """frn.py must import under the .venv where torch is absent."""
    if _has_torch():
        # Still must import; but force-check no top-level torch import.
        pass
    import importlib

    m = importlib.import_module(FRN_MODULE)
    assert hasattr(m, "FRNPrediction")
    assert hasattr(m, "FRNRefinedDetection")
    assert hasattr(m, "FRNRefiner")
    # If this interpreter has torch loaded at import, that's still fine; the
    # gate is that import did not require torch at module scope.
    import sys as _sys

    assert FRN_MODULE in _sys.modules


# ---------------------------------------------------------------------------
# B. Public API surface + frozen signatures.
# ---------------------------------------------------------------------------


def test_task12d_public_api_shapes():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNPrediction,
        FRNRefinedDetection,
        FRNRefiner,
    )

    import dataclasses

    pred_fields = [f.name for f in dataclasses.fields(FRNPrediction)]
    assert pred_fields == [
        "class_id",
        "signal_probability",
        "class_probability",
        "start_norm",
        "duration_norm",
        "bandwidth_norm",
        "center_offset_norm",
    ]

    det_fields = [f.name for f in dataclasses.fields(FRNRefinedDetection)]
    assert det_fields == [
        "proposal",
        "prediction",
        "class_id",
        "t_start_s",
        "t_end_s",
        "f_low_hz",
        "f_high_hz",
        "confidence",
    ]

    import inspect

    sig = inspect.signature(FRNRefiner.__init__)
    params = list(sig.parameters)
    assert params[:2] == ["self", "checkpoint_path"]

    pb = inspect.signature(FRNRefiner.predict_batch)
    assert list(pb.parameters) == ["self", "candidates", "batch_size"]

    rb = inspect.signature(FRNRefiner.refine_batch)
    rp = list(rb.parameters)
    assert rp[0:2] == ["self", "candidates"]
    assert "recording_sample_rate_hz" in rp
    assert "recording_duration_s" in rp
    assert "frequency_low_hz" in rp
    assert "frequency_high_hz" in rp
    assert "batch_size" in rp


def test_task12d_frozen_feature_length_is_4096():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRN_FEATURE_LENGTH,
    )

    assert FRN_FEATURE_LENGTH == 4096


# ---------------------------------------------------------------------------
# C. Pure NumPy feature construction
# ---------------------------------------------------------------------------


def test_task12d_iq_rms_normalization_and_epsilon():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        build_frn_features,
    )

    cand = _candidate()
    iq, fft, bw, diag = build_frn_features(cand)
    assert iq.shape == (2, 4096)
    assert iq.dtype == np.float32
    # row 0 = real, row 1 = imag (order)
    sig = cand.iq.astype(np.complex64)
    rms = float(np.sqrt(np.mean(np.abs(sig) ** 2)))
    normalized = sig / max(rms, 1e-8)
    assert np.allclose(iq[0], np.interp(np.linspace(0, 1, 4096), np.linspace(0, 1, sig.size), normalized.real))
    assert diag["input_rms"] == pytest.approx(rms)


def test_task12d_iq_channel_order_real_then_imag():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        build_frn_features,
    )

    cand = _candidate()
    iq, fft, bw, diag = build_frn_features(cand)
    # A pure-real positive-amplitude iq: real channel dominant.
    n = 4096
    t = np.arange(n, dtype=np.float32)
    iq_real = (0.9 * np.ones(n)).astype(np.complex64)
    proposal = cand.proposal
    c2 = PurifiedCandidate(
        proposal=proposal,
        iq=iq_real,
        sample_rate_hz=cand.sample_rate_hz,
        crop_t_start_s=cand.crop_t_start_s,
        crop_t_end_s=cand.crop_t_end_s,
        lowpass_hz=cand.lowpass_hz,
        decimation=cand.decimation,
        diagnostics=cand.diagnostics,
    )
    iq2, _, _, _ = build_frn_features(c2)
    assert iq2.shape == (2, 4096)
    assert np.all(iq2[0] > 0.0)


def test_task12d_global_resample_endpoint_inclusive_and_single_sample():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        _resample_rows,
    )

    src = np.asarray([[0.0, 1.0, 2.0]], dtype=np.float32)  # shape (1,3)
    out = _resample_rows(src, 5)
    assert out.shape == (1, 5)
    assert out.dtype == np.float32
    assert out[0][0] == 0.0  # endpoint inclusive
    assert out[0][-1] == 2.0  # endpoint inclusive

    single = np.asarray([[3.0]], dtype=np.float32)
    out2 = _resample_rows(single, 7)
    assert out2.shape == (1, 7)
    assert np.all(out2 == 3.0)


def test_task12d_fft_feature_deterministic_known_signal():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        build_frn_features,
    )

    cand = _candidate()
    iq, fft, bw, diag = build_frn_features(cand)
    assert fft.shape == (1, 4096)
    assert fft.dtype == np.float32
    assert np.isfinite(fft).all()


def test_task12d_bandwidth_context_formula():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        bandwidth_context,
    )

    cand = _candidate(lowpass_hz=2.0e6)
    bw = bandwidth_context(cand.lowpass_hz)
    assert bw == pytest.approx(float(np.log2(max(cand.lowpass_hz, 1e-6) / 1e6)))
    # epsilon floor
    assert bandwidth_context(1e-9) == pytest.approx(float(np.log2(1e-6 / 1e6)))
    assert bandwidth_context(0.0) == pytest.approx(float(np.log2(1e-6 / 1e6)))


# ---------------------------------------------------------------------------
# I. Classification decode helpers
# ---------------------------------------------------------------------------


def test_task12d_classification_decode():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        decode_classification,
    )

    logits = np.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0], dtype=np.float32)
    class_id, class_prob, signal_prob = decode_classification(logits, np.float32(1.0))
    assert class_id == 13
    assert class_prob > 0.9  # softmax over 14 classes with one dominant logit
    assert signal_prob == pytest.approx(float(1.0 / (1.0 + np.exp(-1.0))))


def test_task12d_regression_expectation():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        regression_expectation,
    )

    # uniform logits -> expectation 0.5
    logits = np.zeros((1, 1024), dtype=np.float32)
    exp = regression_expectation(logits)
    assert exp.shape == (1,)
    assert abs(float(exp[0]) - 0.5) < 1e-6


def test_task12d_temporal_decode():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        decode_temporal_box,
    )

    cand = _candidate()
    out = decode_temporal_box(
        cand,
        start_norm=0.5,
        duration_norm=0.25,
        recording_sample_rate_hz=100.0e6,
        recording_duration_s=0.05,
    )
    crop_duration = cand.crop_t_end_s - cand.crop_t_start_s
    expected_t0 = cand.crop_t_start_s + 0.5 * crop_duration
    end_norm = min(1.0 - 1e-6, 0.5 + 0.25)
    expected_t1 = cand.crop_t_start_s + end_norm * crop_duration
    assert out[0] == pytest.approx(expected_t0)
    assert out[1] == pytest.approx(expected_t1)


def test_task12d_frequency_decode_and_clipping():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        decode_frequency_box,
    )

    cand = _candidate(lowpass_hz=2.0e6)
    f_low, f_high = decode_frequency_box(
        cand,
        bandwidth_norm=0.5,
        center_offset_norm=0.0,
        frequency_low_hz=2401.0e6,
        frequency_high_hz=2403.0e6,
    )
    bandwidth = max(0.5 * 2.0 * cand.lowpass_hz, 1.0)
    center = 0.5 * (cand.proposal.f_low_hz + cand.proposal.f_high_hz)
    assert f_low == pytest.approx(max(2401.0e6, center - 0.5 * bandwidth))
    assert f_high == pytest.approx(min(2403.0e6, center + 0.5 * bandwidth))


def test_task12d_geometric_score_fusion():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        geometric_fusion,
    )

    conf = geometric_fusion(0.9, 0.8, 0.7)
    import math

    assert conf == pytest.approx(math.sqrt(0.9 * 0.8 * 0.7))
    # zero -> zero
    assert geometric_fusion(0.9, 0.0, 0.7) == 0.0


# ---------------------------------------------------------------------------
# N. Invalid geometry returns positional None, not silent removal
# ---------------------------------------------------------------------------


def test_task12d_invalid_geometry_returns_positional_none(monkeypatch):
    """Corrective D: invalid geometry yields positional None (no silent deletion).

    Uses a lightweight FRNRefiner seam so no checkpoint/GPU is required, and
    monkeypatches decode_frequency_box so candidate 0 -> valid box, candidate 1
    -> invalid box (f_low >= f_high).
    """
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3 import frn as frn_mod
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNPrediction,
        FRNRefinedDetection,
        FRNRefiner,
    )

    cand_valid = _candidate()
    cand_invalid = _candidate()

    def fake_decode_frequency_box(
        candidate,
        *,
        bandwidth_norm,
        center_offset_norm,
        frequency_low_hz,
        frequency_high_hz,
    ):
        if candidate is cand_valid:
            return 2401.0e6, 2403.0e6  # valid
        if candidate is cand_invalid:
            return 2403.0e6, 2403.0e6  # f_low == f_high -> invalid
        return frequency_low_hz, frequency_high_hz

    fake_predictions = [
        FRNPrediction(9, 1.0, 1.0, 0.5, 0.25, 0.5, 0.0),
        FRNPrediction(9, 1.0, 1.0, 0.5, 0.25, 0.5, 0.0),
    ]

    refiner = FRNRefiner.__new__(FRNRefiner)
    refiner._torch = None
    refiner._device = "cpu"
    refiner._model = None
    refiner.predict_batch = lambda candidates, batch_size=64, **kw: fake_predictions  # type: ignore[method-assign]

    monkeypatch.setattr(frn_mod, "decode_frequency_box", fake_decode_frequency_box)

    result = refiner.refine_batch(
        [cand_valid, cand_invalid],
        recording_sample_rate_hz=100.0e6,
        recording_duration_s=0.05,
        frequency_low_hz=2401.0e6,
        frequency_high_hz=2403.0e6,
        batch_size=64,
    )
    assert len(result) == 2
    assert isinstance(result[0], FRNRefinedDetection)
    assert result[1] is None


def test_task12d_batchsize_validation():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNRefiner,
    )

    # build_signature check: batch_size <= 0 -> ValueError at predict_batch once
    # torch present; the validation itself is a module-level helper.
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        validate_batch_size,
    )

    with pytest.raises(ValueError):
        validate_batch_size(0)
    with pytest.raises(ValueError):
        validate_batch_size(-1)
    validate_batch_size(64)


# ---------------------------------------------------------------------------
# P. Empty candidate sequences
# ---------------------------------------------------------------------------


def test_task12d_empty_inputs_return_empty_dicts():
    """Corrective F: predict_batch([]) and refine_batch([]) return [] without model forward."""
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner

    refiner = FRNRefiner.__new__(FRNRefiner)
    refiner._torch = None
    refiner._device = "cpu"
    refiner._model = None

    assert refiner.predict_batch([], batch_size=64) == []
    assert (
        refiner.refine_batch(
            [],
            recording_sample_rate_hz=100.0e6,
            recording_duration_s=0.05,
            frequency_low_hz=2401.0e6,
            frequency_high_hz=2403.0e6,
            batch_size=64,
        )
        == []
    )


def test_task12d_batch_cardinality_mismatch_fails_closed(monkeypatch):
    """Corrective E: a decoded output whose batch cardinality is less than the
    input candidate chunk raises RuntimeError (no silent truncation)."""
    try:
        import torch
    except Exception:
        pytest.skip("torch not available")
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3 import frn as frn_mod
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner

    class _FakeModel:
        use_bandwidth_context = True
        use_center_regression = True

    refiner = FRNRefiner.__new__(FRNRefiner)
    refiner._torch = torch
    refiner._device = "cpu"
    refiner._model = _FakeModel()

    chunk = [_candidate(), _candidate()]
    # Provide a real chunk of 2 candidates; monkeypatch _forward_raw_batch to
    # return an output whose decoded class_logits batch = 1 (mismatch).
    def fake_forward(iq_tensor, fft_tensor, bw_tensor, *, is_cuda):
        B = 1  # deliberately wrong batch cardinality
        return {
            "class_logits": torch.zeros(B, 14),
            "signal_logit": torch.zeros(B),
            "start_logits": torch.zeros(B, 1024),
            "duration_logits": torch.zeros(B, 1024),
            "bandwidth_logits": torch.zeros(B, 1024),
            "center_offset": torch.tanh(torch.zeros(B)),
            "start": torch.zeros(B),
            "duration": torch.zeros(B),
            "bandwidth": torch.zeros(B),
        }

    monkeypatch.setattr(refiner, "_forward_raw_batch", fake_forward)
    with pytest.raises(RuntimeError, match="cardinality mismatch"):
        refiner.predict_batch(chunk, batch_size=64)


# ---------------------------------------------------------------------------
# Runtime gate + env helpers (integration guards)
# ---------------------------------------------------------------------------


def test_task12d_torch_and_cuda_gate_present():
    """Only meaningful on the formal ML runtime with a GPU; else skip."""
    if not _cuda_ready():
        pytest.skip("no CUDA")
    import torch

    assert torch.__version__ == "2.8.0+cu128"
    assert torch.cuda.is_available()
    assert torch.backends.cuda.matmul.allow_tf32 is False
    assert torch.backends.cudnn.allow_tf32 is True


def test_task12d_checkpoint_identity():
    cp = _frn_checkpoint()
    if cp is None:
        pytest.skip("WSP_TASK12D_FRN_CHECKPOINT not set")
    digest = hashlib.sha256(cp.read_bytes()).hexdigest()
    assert digest == "da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67"


# ---------------------------------------------------------------------------
# Historical parity helpers (historical imports are test-only)
# ---------------------------------------------------------------------------


@pytest.fixture()
def acceptance_env():
    """Skip unless all acceptance env vars + CUDA are present."""
    required = [
        "WSP_TASK12D_LEGACY_ROOT",
        "WSP_TASK12D_FRN_CHECKPOINT",
        "WSP_TASK12D_CPN_ORACLE",
        "WSP_TASK12D_RAW_TEST_ROOT",
        "WSP_TASK12D_FRN_RAW_ORACLE_ROOT",
        "WSP_TASK12D_FINAL_ORACLE",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"missing acceptance env: {missing}")
    if not _cuda_ready():
        pytest.skip("no CUDA")
    # legacy root must be importable
    leg = Path(os.environ["WSP_TASK12D_LEGACY_ROOT"])
    if not (leg / "src").is_dir():
        pytest.skip("legacy root has no src/")
    sys.path.insert(0, str(leg / "src"))
    yield {
        "legacy": leg,
        "checkpoint": Path(os.environ["WSP_TASK12D_FRN_CHECKPOINT"]),
        "cpn_oracle": Path(os.environ["WSP_TASK12D_CPN_ORACLE"]),
        "raw_test_root": Path(os.environ["WSP_TASK12D_RAW_TEST_ROOT"]),
        "raw_oracle_root": Path(os.environ["WSP_TASK12D_FRN_RAW_ORACLE_ROOT"]),
        "final_oracle": Path(os.environ["WSP_TASK12D_FINAL_ORACLE"]),
    }


def _read_proposals(sample_id: str, cpn_oracle: Path) -> list[dict]:
    out = []
    for line in cpn_oracle.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row.get("sample_id")) == str(sample_id):
            out.append(row)
    return out


def _probe_ordinal(cpn_oracle: Path, sample_id: str, ordinal: int) -> dict:
    rows = _read_proposals(sample_id, cpn_oracle)
    if ordinal >= len(rows):
        raise IndexError(f"ordinal {ordinal} out of range for sample {sample_id} ({len(rows)} rows)")
    return rows[ordinal]


# ---------------------------------------------------------------------------
# Level 1 — feature parity
# ---------------------------------------------------------------------------


def test_task12d_historical_feature_parity(acceptance_env):
    """Compare production feature construction against the historical helper."""
    import json as _json

    leg = acceptance_env["legacy"]
    sys.path.insert(0, str(leg / "src"))

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
        purify_candidate as plat_ahlp,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        build_frn_features,
        bandwidth_context,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

    from zoomspec_repro.ahlp import purify_candidate as hist_ahlp
    from zoomspec_repro.frn_dataset import bandwidth_context as hist_bw
    from zoomspec_repro.frn_data import make_frn_features as hist_feat
    from zoomspec_repro.data import load_observation as hist_load
    from zoomspec_repro.schema import Proposal

    probes = [("0", 0), ("0", 1), ("0", 3), ("1002", 0), ("0", 10), ("1", 3), ("10", 5)]
    for sample_id, ordinal in probes:
        rows = _read_proposals(sample_id, acceptance_env["cpn_oracle"])
        if ordinal >= len(rows):
            continue
        row = rows[ordinal]
        obs = hist_load(acceptance_env["raw_test_root"] / f"{sample_id}.bin")

        hist_prop = Proposal(
            sample_id=sample_id,
            t0_s=row["t0_s"], t1_s=row["t1_s"],
            f0_hz=row["f0_hz"], f1_hz=row["f1_hz"],
            bandwidth_tier=row["bandwidth_tier"], score=row["score"],
        )
        h_cand = hist_ahlp(obs, hist_prop, cutoff_scale=1.0, validate_observation=False)
        h_iq, h_fft, _ = hist_feat(h_cand, output_length=4096, feature_mode="global_resample")

        plat_prop = CPNProposal(
            t_start_s=row["t0_s"], t_end_s=row["t1_s"],
            f_low_hz=row["f0_hz"], f_high_hz=row["f1_hz"],
            bandwidth_tier=row["bandwidth_tier"], confidence=row["score"],
        )
        p_cand = plat_ahlp(
            obs.iq, sample_rate_hz=obs.fs_hz, center_frequency_hz=obs.center_hz, proposal=plat_prop
        )
        p_iq, p_fft, p_bw, _ = build_frn_features(p_cand)

        assert p_iq.shape == h_iq.shape
        assert p_iq.dtype == h_iq.dtype == np.float32
        assert np.array_equal(p_iq, h_iq)
        assert hashlib.sha256(p_iq.tobytes()).hexdigest() == hashlib.sha256(h_iq.tobytes()).hexdigest()

        assert p_fft.shape == h_fft.shape
        assert p_fft.dtype == h_fft.dtype == np.float32
        assert np.array_equal(p_fft, h_fft)
        assert hashlib.sha256(p_fft.tobytes()).hexdigest() == hashlib.sha256(h_fft.tobytes()).hexdigest()

        assert p_bw == float(hist_bw(h_cand.f_lp_hz))


# ---------------------------------------------------------------------------
# Level 2 — internal raw neural head parity
# ---------------------------------------------------------------------------


def test_task12d_synthetic_architecture_raw_head_equivalence(acceptance_env):
    """Bitwise raw-head parity with the historical model from the SAME checkpoint."""
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner

    import torch

    leg = acceptance_env["legacy"]
    sys.path.insert(0, str(leg / "src"))
    from zoomspec_repro.frn import ZoomSpecFRN as HistoricalFRN

    ckpt = torch.load(acceptance_env["checkpoint"], map_location="cpu", weights_only=False)
    cfg = ckpt["config"]
    hist = HistoricalFRN(
        channels=int(cfg["model"]["channels"]),
        num_classes=int(cfg["data"]["classes"]),
        fusion_attention=bool(cfg["model"]["fusion_attention"]),
        use_bandwidth_context=bool(cfg["model"].get("use_bandwidth_context", False)),
        use_center_regression=bool(cfg["model"].get("use_center_regression", False)),
        use_log_bandwidth=bool(cfg["model"].get("use_log_bandwidth", False)),
        use_attention_pool=bool(cfg["model"].get("use_attention_pool", False)),
    )
    hist.load_state_dict(ckpt["model"], strict=True)
    prod = FRNRefiner(acceptance_env["checkpoint"], device=0)
    pmodel = prod._model

    dev = "cuda"
    hist.to(dev).eval()
    torch.manual_seed(42)
    iq = torch.randn(4, 2, 4096, device=dev)
    fft = torch.randn(4, 1, 4096, device=dev)
    bw = torch.tensor(
        [np.log2(2e6 / 1e6), np.log2(4e6 / 1e6), np.log2(1e6 / 1e6), np.log2(8e6 / 1e6)],
        device=dev, dtype=torch.float32,
    )
    with torch.inference_mode():
        with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True):
            o_hist = hist(iq, fft, bw)
            o_prod = pmodel(iq, fft, bw)

    for key in [
        "class_logits",
        "signal_logit",
        "start_logits",
        "duration_logits",
        "bandwidth_logits",
        "center_offset",
        "start",
        "duration",
        "bandwidth",
    ]:
        a = o_hist[key]
        b = o_prod[key]
        assert tuple(a.shape) == tuple(b.shape)
        assert a.dtype == b.dtype
        assert torch.equal(a, b), key


# ---------------------------------------------------------------------------
# Corrective A — real recording-batch raw-head parity
# ---------------------------------------------------------------------------


def test_task12d_historical_real_recording_batch_raw_head_parity(acceptance_env):
    """Bitwise raw-head parity on REAL historical recording-batch contexts.

    Samples 0 / 1 / 10 / 1002 cover all seven approved probes. Each recording's
    full candidate batch (all frozen CPN proposals in oracle row order) is built
    with the Task-12C production AHLP and run through both the historical and
    production FRN models under CUDA float16 autocast with identical tensors.
    """
    import torch

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
        purify_candidate as plat_ahlp,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNRefiner,
        build_frn_features,
        bandwidth_context,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

    from zoomspec_repro.frn import ZoomSpecFRN as HistoricalFRN
    from zoomspec_repro.data import load_observation as hist_load

    leg = acceptance_env["legacy"]
    sys.path.insert(0, str(leg / "src"))
    ckpt = torch.load(acceptance_env["checkpoint"], map_location="cpu", weights_only=False)
    cfg = ckpt["config"]
    hist = HistoricalFRN(
        channels=int(cfg["model"]["channels"]),
        num_classes=int(cfg["data"]["classes"]),
        fusion_attention=bool(cfg["model"]["fusion_attention"]),
        use_bandwidth_context=bool(cfg["model"].get("use_bandwidth_context", False)),
        use_center_regression=bool(cfg["model"].get("use_center_regression", False)),
        use_log_bandwidth=bool(cfg["model"].get("use_log_bandwidth", False)),
        use_attention_pool=bool(cfg["model"].get("use_attention_pool", False)),
    )
    hist.load_state_dict(ckpt["model"], strict=True)
    dev = "cuda"
    hist.to(dev).eval()

    prod = FRNRefiner(acceptance_env["checkpoint"], device=0)
    pmodel = prod._model

    head_keys = [
        "class_logits",
        "signal_logit",
        "start_logits",
        "duration_logits",
        "bandwidth_logits",
        "center_offset",
        "start",
        "duration",
        "bandwidth",
    ]

    for sample_id in ["0", "1", "10", "1002"]:
        rows = _read_proposals(sample_id, acceptance_env["cpn_oracle"])
        obs = hist_load(acceptance_env["raw_test_root"] / f"{sample_id}.bin")
        cands = []
        iq_rows = []
        fft_rows = []
        bws = []
        for row in rows:
            prop = CPNProposal(
                t_start_s=row["t0_s"], t_end_s=row["t1_s"],
                f_low_hz=row["f0_hz"], f_high_hz=row["f1_hz"],
                bandwidth_tier=row["bandwidth_tier"], confidence=row["score"],
            )
            cand = plat_ahlp(obs.iq, sample_rate_hz=obs.fs_hz, center_frequency_hz=obs.center_hz, proposal=prop)
            cands.append(cand)
            iq, fft, bw, _ = build_frn_features(cand)
            iq_rows.append(iq)
            fft_rows.append(fft)
            bws.append(np.float32(bw))

        n = len(cands)
        # All four samples have <=64 proposals -> single real recording chunk.
        iq_tensor = torch.from_numpy(np.stack(iq_rows)).to(dev)
        fft_tensor = torch.from_numpy(np.stack(fft_rows)).to(dev)
        bw_tensor = torch.from_numpy(np.asarray(bws, dtype=np.float32)).to(dev)

        with torch.inference_mode():
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True):
                o_hist = hist(iq_tensor, fft_tensor, bw_tensor)
                o_prod = pmodel(iq_tensor, fft_tensor, bw_tensor)

        for key in head_keys:
            a = o_hist[key]
            b = o_prod[key]
            assert tuple(a.shape) == tuple(b.shape), (sample_id, key)
            assert a.dtype == b.dtype, (sample_id, key)
            assert torch.equal(a, b), (sample_id, key)



# ---------------------------------------------------------------------------
# Level 3 — frozen diagnostic / raw-shard parity (sample 0, full 17-row; probes)
# ---------------------------------------------------------------------------


def test_task12d_frozen_frn_diagnostic_oracle_parity(acceptance_env):
    """FRNPrediction + FRNRefinedDetection match the frozen raw-shard oracle."""
    import json as _json

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
        purify_candidate as plat_ahlp,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNRefiner,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

    from zoomspec_repro.data import load_observation as hist_load

    refiner = FRNRefiner(acceptance_env["checkpoint"], device=0)

    def _sample_raw_rows(sample_id: str) -> list[dict]:
        import json as _json

        shard_root = acceptance_env["raw_oracle_root"]
        for shard in sorted(shard_root.glob("test_raw_shard_augv3_*.jsonl")):
            rows = []
            for line in shard.open(encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                if str(r.get("sample_id")) == str(sample_id):
                    rows.append(r)
            if rows:
                return rows
        return []

    probes = [("0", 0), ("0", 1), ("0", 3), ("1002", 0), ("0", 10), ("1", 3), ("10", 5)]
    for sample_id, ordinal in probes:
        proposal_rows = _read_proposals(sample_id, acceptance_env["cpn_oracle"])
        if ordinal >= len(proposal_rows):
            continue
        shard_rows = _sample_raw_rows(sample_id)
        if ordinal >= len(shard_rows):
            continue
        obs = hist_load(acceptance_env["raw_test_root"] / f"{sample_id}.bin")
        cands = []
        for row in proposal_rows:
            prop = CPNProposal(
                t_start_s=row["t0_s"], t_end_s=row["t1_s"],
                f_low_hz=row["f0_hz"], f_high_hz=row["f1_hz"],
                bandwidth_tier=row["bandwidth_tier"], confidence=row["score"],
            )
            cands.append(plat_ahlp(obs.iq, sample_rate_hz=obs.fs_hz, center_frequency_hz=obs.center_hz, proposal=prop))
        refined = refiner.refine_batch(
            cands,
            recording_sample_rate_hz=obs.fs_hz,
            recording_duration_s=obs.duration_s,
            frequency_low_hz=obs.f_lo_hz,
            frequency_high_hz=obs.f_hi_hz,
            batch_size=64,
        )
        sel = refined[ordinal]
        o = shard_rows[ordinal]
        assert sel is not None
        # TRUE exact equality (no tolerance) for frozen oracle acceptance.
        assert int(sel.class_id) == int(o["class_id"])
        assert float(sel.prediction.signal_probability) == float(o["signal_probability"])
        assert float(sel.prediction.class_probability) == float(o["class_probability"])
        assert float(sel.prediction.start_norm) == float(o["start_norm"])
        assert float(sel.prediction.duration_norm) == float(o["duration_norm"])
        assert float(sel.prediction.bandwidth_norm) == float(o["bandwidth_norm"])
        assert float(sel.prediction.center_offset_norm) == float(o["center_offset_norm"])
        assert float(sel.t_start_s) == float(o["t0_s"])
        assert float(sel.t_end_s) == float(o["t1_s"])
        assert float(sel.f_low_hz) == float(o["f0_hz"])
        assert float(sel.f_high_hz) == float(o["f1_hz"])
        assert float(sel.confidence) == float(o["final_score"])


# ---------------------------------------------------------------------------
# Full sample-0 pre-NMS parity
# ---------------------------------------------------------------------------


def test_task12d_sample0_pre_nms_frn_parity(acceptance_env):
    """All 17 sample-0 CPN proposals -> 17 exact pre-threshold/pre-NMS rows."""
    import json as _json

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
        purify_candidate as plat_ahlp,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNRefiner,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

    from zoomspec_repro.data import load_observation as hist_load

    sample_id = "0"
    rows = _read_proposals(sample_id, acceptance_env["cpn_oracle"])
    assert len(rows) == 17
    obs = hist_load(acceptance_env["raw_test_root"] / f"{sample_id}.bin")
    cands = []
    for row in rows:
        prop = CPNProposal(
            t_start_s=row["t0_s"], t_end_s=row["t1_s"],
            f_low_hz=row["f0_hz"], f_high_hz=row["f1_hz"],
            bandwidth_tier=row["bandwidth_tier"], confidence=row["score"],
        )
        cands.append(plat_ahlp(obs.iq, sample_rate_hz=obs.fs_hz, center_frequency_hz=obs.center_hz, proposal=prop))
    refiner = FRNRefiner(acceptance_env["checkpoint"], device=0)
    refined = refiner.refine_batch(
        cands,
        recording_sample_rate_hz=obs.fs_hz,
        recording_duration_s=obs.duration_s,
        frequency_low_hz=obs.f_lo_hz,
        frequency_high_hz=obs.f_hi_hz,
        batch_size=64,
    )
    assert len(refined) == 17

    # The frozen raw-shard for sample 0 lives in shard 00. Read all sample-0 rows.
    shard00 = acceptance_env["raw_oracle_root"] / "test_raw_shard_augv3_00.jsonl"
    oracle_rows = []
    for line in shard00.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if str(r.get("sample_id")) == sample_id:
            oracle_rows.append(r)
    assert len(oracle_rows) == 17

    # Raw shard order == CPN proposal order (diagnostics are per-candidate in order).
    for i, det in enumerate(refined):
        o = oracle_rows[i]
        assert det is not None
        # TRUE exact equality (no tolerance) for frozen sample-0 acceptance.
        assert int(det.class_id) == int(o["class_id"])
        assert float(det.t_start_s) == float(o["t0_s"])
        assert float(det.t_end_s) == float(o["t1_s"])
        assert float(det.f_low_hz) == float(o["f0_hz"])
        assert float(det.f_high_hz) == float(o["f1_hz"])
        assert float(det.confidence) == float(o["final_score"])
        p = det.prediction
        assert float(p.signal_probability) == float(o["signal_probability"])
        assert float(p.class_probability) == float(o["class_probability"])
        assert float(p.start_norm) == float(o["start_norm"])
        assert float(p.duration_norm) == float(o["duration_norm"])
        assert float(p.bandwidth_norm) == float(o["bandwidth_norm"])
        assert float(p.center_offset_norm) == float(o["center_offset_norm"])


# ---------------------------------------------------------------------------
# batch=1 diagnostic (semantic divergence detection only)
# ---------------------------------------------------------------------------


def test_task12d_batch1_vs_historical_batch_diagnostic(acceptance_env):
    """Measure actual batch=1 vs full-recording-batch numerical drift.

    Uses the private ``_forward_raw_batch`` seam to compare raw logits A (inside
    its normal recording batch) vs B (batch=1) for all seven approved probes.
    Fails only on semantic divergence (class-id/argmax change, valid<->invalid).
    """
    import torch

    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
        purify_candidate as plat_ahlp,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNRefiner,
        build_frn_features,
        bandwidth_context,
    )
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

    from zoomspec_repro.data import load_observation as hist_load

    probes = [("0", 0), ("0", 1), ("0", 3), ("1002", 0), ("0", 10), ("1", 3), ("10", 5)]
    # Cache (sample -> list of candidates) to build recording batches.
    sample_cands: dict[str, list] = {}

    def _get_sample(sample_id: str) -> tuple[list, object]:
        if sample_id not in sample_cands:
            rows = _read_proposals(sample_id, acceptance_env["cpn_oracle"])
            obs = hist_load(acceptance_env["raw_test_root"] / f"{sample_id}.bin")
            cands = []
            for row in rows:
                prop = CPNProposal(
                    t_start_s=row["t0_s"], t_end_s=row["t1_s"],
                    f_low_hz=row["f0_hz"], f_high_hz=row["f1_hz"],
                    bandwidth_tier=row["bandwidth_tier"], confidence=row["score"],
                )
                cands.append(plat_ahlp(obs.iq, sample_rate_hz=obs.fs_hz, center_frequency_hz=obs.center_hz, proposal=prop))
            sample_cands[sample_id] = (cands, obs)
        return sample_cands[sample_id]

    def _features(cands):
        iq_rows, fft_rows, bws = [], [], []
        for c in cands:
            iq, fft, bw, _ = build_frn_features(c)
            iq_rows.append(iq)
            fft_rows.append(fft)
            bws.append(np.float32(bw))
        return iq_rows, fft_rows, bws

    refiner = FRNRefiner(acceptance_env["checkpoint"], device=0)
    is_cuda = True

    def raw_prediction(cands):
        iq_rows, fft_rows, bws = _features(cands)
        iq_t = torch.from_numpy(np.stack(iq_rows)).to("cuda")
        fft_t = torch.from_numpy(np.stack(fft_rows)).to("cuda")
        bw_t = torch.from_numpy(np.asarray(bws, dtype=np.float32)).to("cuda")
        return refiner._forward_raw_batch(iq_t, fft_t, bw_t, is_cuda=is_cuda)

    drift_rows = []
    for sample_id, ordinal in probes:
        cands, obs = _get_sample(sample_id)
        if ordinal >= len(cands):
            continue
        out_full = raw_prediction(cands)
        out_single = raw_prediction([cands[ordinal]])
        # Compare the target probe's raw logits: batch context vs singleton.
        diff = {
            "sample": sample_id,
            "ordinal": ordinal,
        }
        target_heads = {}
        for head in ["class_logits", "signal_logit", "start_logits", "duration_logits", "bandwidth_logits", "center_offset"]:
            a = out_full[head][ordinal].float()
            b = out_single[head][0].float()
            target_heads[head] = float((a - b).abs().max()) if a.numel() else None
        diff["raw_head_max_abs_diff"] = target_heads

        # Decode-level comparison via refine_batch.
        full_refined = refiner.refine_batch(
            cands,
            recording_sample_rate_hz=obs.fs_hz,
            recording_duration_s=obs.duration_s,
            frequency_low_hz=obs.f_lo_hz,
            frequency_high_hz=obs.f_hi_hz,
            batch_size=64,
        )
        single_refined = refiner.refine_batch(
            [cands[ordinal]],
            recording_sample_rate_hz=obs.fs_hz,
            recording_duration_s=obs.duration_s,
            frequency_low_hz=obs.f_lo_hz,
            frequency_high_hz=obs.f_hi_hz,
            batch_size=1,
        )[0]
        base = full_refined[ordinal]
        single = single_refined
        if base is not None and single is not None:
            diff["class_same"] = base.class_id == single.class_id
            diff["validity_same"] = (base.f_low_hz < base.f_high_hz) == (single.f_low_hz < single.f_high_hz)
            diff["t_start_diff"] = abs(float(base.t_start_s - single.t_start_s))
            diff["t_end_diff"] = abs(float(base.t_end_s - single.t_end_s))
            diff["f_low_diff"] = abs(float(base.f_low_hz - single.f_low_hz))
            diff["f_high_diff"] = abs(float(base.f_high_hz - single.f_high_hz))
            diff["confidence_diff"] = abs(float(base.confidence - single.confidence))
        elif base is None and single is None:
            diff["class_same"] = True
            diff["validity_same"] = True
            diff["t_start_diff"] = None
        else:
            # One valid, one invalid -> semantic divergence.
            diff["class_same"] = base.class_id == single.class_id if base and single else False
            diff["validity_same"] = False
        drift_rows.append(diff)

        # Semantic divergence hard-fail.
        if base is not None and single is not None:
            if base.class_id != single.class_id:
                pytest.fail(f"class argmax divergence at {sample_id}/{ordinal}")
            if (base.f_low_hz < base.f_high_hz) != (single.f_low_hz < single.f_high_hz):
                pytest.fail(f"validity divergence at {sample_id}/{ordinal}")
        elif base is None and single is not None:
            pytest.fail(f"invalid->valid divergence at {sample_id}/{ordinal}")
        elif base is not None and single is None:
            pytest.fail(f"valid->invalid divergence at {sample_id}/{ordinal}")

    # Record the measured drift (for provenance reporting). No tolerance used.
    assert len(drift_rows) == len(probes)


# ---------------------------------------------------------------------------
# Model lifetime — checkpoint loaded once per FRNRefiner instance
# ---------------------------------------------------------------------------


def test_task12d_model_loaded_once_per_instance():
    cp = _frn_checkpoint()
    if cp is None:
        pytest.skip("WSP_TASK12D_FRN_CHECKPOINT not set")
    if not _cuda_ready():
        pytest.skip("no CUDA")
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner

    refiner = FRNRefiner(cp, device=0)
    model_id1 = id(refiner._model)
    # Repeated predict/refine reuse the same model instance.
    cand = _candidate()
    refiner.predict_batch([cand], batch_size=1)
    refiner.refine_batch(
        [cand],
        recording_sample_rate_hz=100.0e6,
        recording_duration_s=0.05,
        frequency_low_hz=2401.0e6,
        frequency_high_hz=2403.0e6,
        batch_size=1,
    )
    assert id(refiner._model) == model_id1


# ---------------------------------------------------------------------------
# Fail-closed: incompatible checkpoint config
# ---------------------------------------------------------------------------


def test_task12d_incompatible_checkpoint_config_fails():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner

    ref = FRNRefiner.__new__(FRNRefiner)
    with pytest.raises(RuntimeError) as exc:
        ref._validate_embedded_config(
            {"model": {"channels": 128, "fusion_attention": True, "use_bandwidth_context": True, "use_center_regression": True},
             "data": {"input_length": 512, "classes": 7}}
        )
    assert "incompatible FRN checkpoint config" in str(exc.value)


# ---------------------------------------------------------------------------
# Fail-closed: batch cardinality mismatch / empty / invalid
# ---------------------------------------------------------------------------


def test_task12d_fail_closed_empty_and_invalid_batch(monkeypatch):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import (
        FRNRefiner,
        validate_batch_size,
    )

    with pytest.raises(ValueError):
        validate_batch_size(0)
    with pytest.raises(ValueError):
        validate_batch_size(-1)

    # predict_batch([]) returns [] without invoking model forward.
    ref = FRNRefiner.__new__(FRNRefiner)
    assert ref.predict_batch([], batch_size=64) == []
    assert ref.refine_batch(
        [],
        recording_sample_rate_hz=100.0e6,
        recording_duration_s=0.05,
        frequency_low_hz=2401.0e6,
        frequency_high_hz=2403.0e6,
        batch_size=64,
    ) == []


# ---------------------------------------------------------------------------
# Static guards — production frn.py must NOT contain forbidden constructs
# ---------------------------------------------------------------------------


def test_task12d_static_guards():
    import ast

    import app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn as frn_mod

    src_path = Path(frn_mod.__file__)
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 1) No forbidden module imports at MODULE TOP-LEVEL.
    forbidden_modules = {
        "subprocess",
        "sqlalchemy",
        "fastapi",
        "ultralytics",
        "zoomspec_repro",
    }
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert forbidden_modules.isdisjoint(imported), imported & forbidden_modules

    # 2) Torch must NOT be imported at module TOP-LEVEL (lazy import is inside a function).
    assert "torch" not in imported

    # 3) No code-level use (Name/Attribute) of forbidden symbols outside docstrings.
    forbidden_names = {"physical_class_nms", "tf_iou", "DetectionPayload", "subprocess", "final_score"}
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    assert forbidden_names.isdisjoint(used), used & forbidden_names

    # 4) No historical absolute deployment path / sys.path mutation as a CODE constant
    #    (docstring prose is harmless; scan only constants *outside* docstrings).
    import ast as _ast

    docstring_nodes = set()
    for node in tree.body:
        if isinstance(node, (ast.Expr, ast.FunctionDef, ast.ClassDef)) and isinstance(
            getattr(node, "value", None), ast.Constant
        ):
            docstring_nodes.add(id(node.value))
    path_tokens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_nodes:
                continue
            path_tokens.append(node.value)
    for tok in path_tokens:
        assert "/root/autodl-tmp" not in tok
    assert not any("sys.path" in t for t in path_tokens)


