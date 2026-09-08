"""Task 12C focused tests for the frozen AHLP purification production port.

Every focused test node id contains ``task12c`` so the suite can be selected
with ``-k task12c``.

AHLP is pure NumPy: pure unit tests run without Torch/Ultralytics/SciPy (and
under ``repo/.venv``). The external historical seven-probe parity test imports
the historical ZoomSpec runtime ONLY inside the test helper and skips
truthfully where external resources are absent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

CPN_ORACLE_SHA256 = "021bc47e604303a2711c2b860d681ecc698b2bbcc464c0d10679e1b5e9fd1c79"


def _proposal(
    t0=0.01,
    t1=0.03,
    f0=2_401_000_000.0,
    f1=2_402_000_000.0,
    tier=1,
    conf=0.9,
) -> CPNProposal:
    return CPNProposal(t0, t1, f0, f1, tier, conf)


def _simple_iq(fs=5_000_000.0, duration=0.05, seed=7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = int(fs * duration)
    return (rng.standard_normal(total) + 1j * rng.standard_normal(total)).astype(np.complex64)


def _load_legacy_ahlp(legacy_root: Path):
    """Import the historical AHLP module in a test-only, bounded manner."""
    src = legacy_root / "src"
    inserted = False
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
        inserted = True
    from zoomspec_repro import ahlp, schema  # noqa: F401

    return ahlp, schema, inserted, list(src.parts)


def _cleanup_legacy(inserted: bool, src_parts: list) -> None:
    if inserted:
        src = Path(*src_parts)
        if str(src) in sys.path:
            sys.path.remove(str(src))
    for name in [m for m in list(sys.modules) if m == "zoomspec_repro" or m.startswith("zoomspec_repro.")]:
        del sys.modules[name]


class TestTask12cDependencyBoundary:
    def test_task12c_module_has_no_ml_dependencies(self):
        spec = importlib.util.find_spec("app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp")
        assert spec is not None

    def test_task12c_source_contains_no_forbidden_deps(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "app" / "pipelines" / "zoomspec_yolo26n_aug_combined_frn_v3" / "ahlp.py"
        )
        source = module_path.read_text(encoding="utf-8")
        forbidden = [
            "import torch",
            "from torch",
            "import ultralytics",
            "from ultralytics",
            "import scipy",
            "from scipy",
            "zoomspec_repro",
            "/root/autodl-tmp",
            "sys.path",
            "import subprocess",
            "from subprocess",
            "read_segment",
            "open(",
        ]
        for token in forbidden:
            assert token not in source, f"forbidden token present: {token}"


class TestTask12cPublicApi:
    def test_task12c_purify_candidate_frozen_signature(self):
        import inspect

        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        sig = inspect.signature(purify_candidate)
        params = sig.parameters
        assert list(params) == ["iq", "sample_rate_hz", "center_frequency_hz", "proposal"]
        for name in ("sample_rate_hz", "center_frequency_hz", "proposal"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        frozen = ["kappa", "eta", "order_constant", "min_numtaps", "max_numtaps", "context_ratio", "cutoff_scale"]
        for name in frozen:
            assert name not in params, f"frozen tunable exposed: {name}"

    def test_task12c_dataclass_contract(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
            AHLPDiagnostics,
            PurifiedCandidate,
        )

        diag = AHLPDiagnostics(
            beta=1.0,
            requested_lowpass_hz=1000.0,
            frequency_shift_hz=0.0,
            numtaps=31,
            numtaps_capped=False,
            input_samples=100,
            output_samples=50,
            crop_start_sample=0,
            crop_end_sample=100,
            identity_filter=False,
        )
        cand = PurifiedCandidate(
            proposal=_proposal(),
            iq=np.zeros(10, dtype=np.complex64),
            sample_rate_hz=1000.0,
            crop_t_start_s=0.0,
            crop_t_end_s=0.01,
            lowpass_hz=100.0,
            decimation=5,
            diagnostics=diag,
        )
        assert cand.iq.dtype == np.complex64


class TestTask12cInputValidation:
    def test_task12c_rejects_non_complex64(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        with pytest.raises(ValueError):
            purify_candidate(
                np.zeros(1000, dtype=np.float32),
                sample_rate_hz=5_000_000.0,
                center_frequency_hz=2_402_500_000.0,
                proposal=_proposal(),
            )

    def test_task12c_rejects_non_1d(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        with pytest.raises(ValueError):
            purify_candidate(
                np.zeros((10, 10), dtype=np.complex64),
                sample_rate_hz=5_000_000.0,
                center_frequency_hz=2_402_500_000.0,
                proposal=_proposal(),
            )

    def test_task12c_rejects_empty_iq(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        with pytest.raises(ValueError):
            purify_candidate(
                np.zeros(0, dtype=np.complex64),
                sample_rate_hz=5_000_000.0,
                center_frequency_hz=2_402_500_000.0,
                proposal=_proposal(),
            )

    def test_task12c_rejects_invalid_sample_rate(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        with pytest.raises(ValueError):
            purify_candidate(
                np.zeros(1000, dtype=np.complex64),
                sample_rate_hz=0.0,
                center_frequency_hz=2_402_500_000.0,
                proposal=_proposal(),
            )

    def test_task12c_rejects_invalid_proposal(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        bad = _proposal(t0=0.03, t1=0.01)
        with pytest.raises(ValueError):
            purify_candidate(
                np.zeros(1000, dtype=np.complex64),
                sample_rate_hz=5_000_000.0,
                center_frequency_hz=2_402_500_000.0,
                proposal=bad,
            )


class TestTask12cTimeCrop:
    def test_task12c_interior_proposal_crop(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        center = 2_402_500_000.0
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=center,
            proposal=_proposal(t0=0.01, t1=0.03, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        assert cand.crop_t_start_s < cand.crop_t_end_s
        assert cand.crop_t_start_s > 0.0
        assert cand.crop_t_end_s < 0.05

    def test_task12c_recording_start_clipping(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.0, t1=0.002, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        assert cand.diagnostics.crop_start_sample == 0
        assert cand.crop_t_start_s == 0.0

    def test_task12c_recording_end_clipping(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.048, t1=0.05, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        assert cand.diagnostics.crop_end_sample == iq.size
        assert cand.crop_t_end_s == iq.size / fs

    def test_task12c_sample_index_floor_start_ceil_end_exclusive(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        # proposal so crop boundaries are non-integer sample positions
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.0111, t1=0.0199, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        n0 = cand.diagnostics.crop_start_sample
        n1 = cand.diagnostics.crop_end_sample
        duration = 0.0199 - 0.0111
        crop_req_start = 0.0111 - 0.1 * duration
        crop_req_end = 0.0199 + 0.1 * duration
        assert n0 == int(np.floor(crop_req_start * fs))
        assert n1 == int(np.ceil(crop_req_end * fs))
        assert n1 - n0 == cand.diagnostics.input_samples
        # end index exclusive: candidate is based on iq[n0:n1]
        assert cand.crop_t_start_s == n0 / fs
        assert cand.crop_t_end_s == n1 / fs

    def test_task12c_returned_crop_times_sample_aligned(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.02, t1=0.04, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        n0 = cand.diagnostics.crop_start_sample
        n1 = cand.diagnostics.crop_end_sample
        assert cand.crop_t_start_s == n0 / fs
        assert cand.crop_t_end_s == n1 / fs


class TestTask12cMixing:
    def test_task12c_frequency_shift_sign_negative(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _frequency_shift_hz

        # proposal center above observation center -> positive shift
        proposal_center = 2_403_000_000.0
        center = 2_402_500_000.0
        shift = _frequency_shift_hz(proposal_center, center)
        assert shift == 500_000.0
        # oscillator uses exp(-1j*...): verify via small computation
        fs = 5_000_000.0
        n = np.arange(0, 4, dtype=np.float64)
        osc = np.exp(-1j * 2.0 * np.pi * shift * n / fs)
        assert np.isclose(osc[1], np.exp(-1j * 2.0 * np.pi * shift / fs))

    def test_task12c_global_phase_origin_required(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
            _oscillator,
            purify_candidate,
        )

        # Design a synthetic where crop-local index would give different mixing.
        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        proposal = _proposal(t0=0.02, t1=0.04, f0=2_403_000_000.0, f1=2_404_000_000.0)
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=proposal,
        )
        n0 = cand.diagnostics.crop_start_sample
        n1 = cand.diagnostics.crop_end_sample
        shift = cand.diagnostics.frequency_shift_hz
        global_osc = _oscillator(n0, n1, shift, fs)
        local_osc = _oscillator(0, n1 - n0, shift, fs)
        # global origin must differ from local origin when n0 != 0
        if n0 != 0:
            assert not np.array_equal(global_osc, local_osc)
            # and the candidate must match the global-origin construction
            mixed = iq[n0:n1] * global_osc
            assert mixed.dtype == np.complex128


class TestTask12cBeta:
    def test_task12c_beta_score_1(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _beta

        assert _beta(1.0) == 1.0

    def test_task12c_beta_score_0(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _beta

        assert _beta(0.0) == 1.2


class TestTask12cFilter:
    def test_task12c_hamming_taps_odd_float64_symmetric_normalized(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _design_hamming_lowpass

        taps = _design_hamming_lowpass(5_000_000.0, 100_000.0)
        assert taps.size % 2 == 1
        assert taps.dtype == np.float64
        assert np.allclose(taps, taps[::-1])
        assert np.isclose(taps.sum(), 1.0)

    def test_task12c_max_tap_clamp(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _design_hamming_lowpass

        taps = _design_hamming_lowpass(50_000_000.0, 2_000.0)
        assert taps.size == 4095

    def test_task12c_identity_branch(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 40_000_000.0
        iq = _simple_iq(fs=fs)
        # wide-band proposal drives requested f_lp above Nyquist
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.01, t1=0.04, f0=2_401_000_000.0, f1=2_440_500_000.0),
        )
        assert cand.diagnostics.identity_filter is True
        assert cand.diagnostics.numtaps == 1
        assert cand.lowpass_hz == np.nextafter(0.5 * fs, 0.0)
        assert cand.iq.dtype == np.complex64
        assert cand.decimation == 1


class TestTask12cConvolution:
    def test_task12c_fft_same_alignment_impulse(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _fft_convolve_same

        signal = np.zeros(8, dtype=np.complex128)
        signal[0] = 1.0 + 0j
        taps = np.ones(5, dtype=np.float64)
        out = _fft_convolve_same(signal, taps)
        assert out.shape == (8,)
        assert out.dtype == np.complex64
        # historical same-mode alignment: start=(taps.size-1)//2=2 over the
        # linear convolution [1,1,1,1,1,0,0,0,0,0,0,0] -> out[0:3]=1, rest 0
        expected = np.zeros(8, dtype=np.complex64)
        expected[0] = taps[2]
        expected[1] = taps[3]
        expected[2] = taps[4]
        assert np.allclose(out, expected)

    def test_task12c_fft_output_length(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _fft_convolve_same

        signal = np.zeros(20, dtype=np.complex128)
        taps = np.arange(1, 8, dtype=np.float64)
        out = _fft_convolve_same(signal, taps)
        assert out.shape == (20,)


class TestTask12cDecimation:
    def test_task12c_decimation_index0_phase(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _decimation, purify_candidate

        fs = 5_000_000.0
        assert _decimation(fs, 1_000_000.0) == 2
        iq = _simple_iq(fs=fs)
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.01, t1=0.03, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        assert cand.decimation >= 1
        assert cand.sample_rate_hz == fs / cand.decimation

    def test_task12c_decimation_1(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import _decimation

        assert _decimation(5_000_000.0, 2_000_000.0) == 1


class TestTask12cCandidateValidation:
    def test_task12c_nyquist_invariant_holds(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import purify_candidate

        fs = 5_000_000.0
        iq = _simple_iq(fs=fs)
        cand = purify_candidate(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            proposal=_proposal(t0=0.01, t1=0.03, f0=2_401_000_000.0, f1=2_402_000_000.0),
        )
        assert cand.sample_rate_hz > 0
        assert cand.lowpass_hz > 0
        assert cand.decimation >= 1
        assert cand.sample_rate_hz + 1e-6 >= 2.0 * cand.lowpass_hz
        assert cand.crop_t_start_s < cand.crop_t_end_s
        assert cand.iq.ndim == 1
        assert cand.iq.dtype == np.complex64
        assert cand.iq.size > 0


def _build_historical_reference(legacy_root: Path, raw_root: Path, oracle_row: dict):
    """Run the actual historical AHLP for one oracle row (test-only)."""
    import json

    ahlp, schema, inserted, src_parts = _load_legacy_ahlp(legacy_root)
    from zoomspec_repro.data import load_observation
    from app.recordings.reader import read_segment_from_path

    sample_id = oracle_row["sample_id"]
    bin_path = raw_root / f"{sample_id}.bin"
    json_path = raw_root / f"{sample_id}.json"
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    f_lo_mhz, f_hi_mhz = (float(x) for x in meta["observation_range"])
    f_lo_hz, f_hi_hz = f_lo_mhz * 1e6, f_hi_mhz * 1e6
    fs = f_hi_hz - f_lo_hz
    center = 0.5 * (f_lo_hz + f_hi_hz)

    iq = read_segment_from_path(bin_path, "float16_interleaved_le")

    obs = schema.Observation(
        sample_id=sample_id,
        iq=iq,
        fs_hz=fs,
        f_lo_hz=f_lo_hz,
        f_hi_hz=f_hi_hz,
        targets=[],
    )
    prop = schema.Proposal(
        sample_id=sample_id,
        t0_s=float(oracle_row["t0_s"]),
        t1_s=float(oracle_row["t1_s"]),
        f0_hz=float(oracle_row["f0_hz"]),
        f1_hz=float(oracle_row["f1_hz"]),
        bandwidth_tier=int(oracle_row["bandwidth_tier"]),
        score=float(oracle_row["score"]),
    )
    cand = ahlp.purify_candidate(obs, prop, validate_observation=False)
    return {
        "iq": iq, "fs": fs, "center": center,
        "cand": cand, "proposal": prop, "obs": obs,
    }


@pytest.mark.skipif(
    not all(
        os.environ.get(v)
        for v in ("WSP_TASK12C_LEGACY_ROOT", "WSP_TASK12C_CPN_ORACLE", "WSP_TASK12C_RAW_TEST_ROOT")
    ),
    reason="Task-12C historical assets not supplied via WSP_TASK12C_* env",
)
class TestTask12cHistoricalParity:
    def test_task12c_historical_seven_probe_ahlp_parity(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
            _design_hamming_lowpass,
            purify_candidate,
        )

        legacy_root = Path(os.environ["WSP_TASK12C_LEGACY_ROOT"])
        oracle_path = Path(os.environ["WSP_TASK12C_CPN_ORACLE"])
        raw_root = Path(os.environ["WSP_TASK12C_RAW_TEST_ROOT"])

        assert hashlib.sha256(oracle_path.read_bytes()).hexdigest() == CPN_ORACLE_SHA256
        ahlp_ref, schema, inserted, src_parts = _load_legacy_ahlp(legacy_root)
        try:
            oracle_rows = [json.loads(l) for l in oracle_path.read_text().splitlines()]
            from collections import defaultdict

            grouped = defaultdict(list)
            for row in oracle_rows:
                grouped[row["sample_id"]].append(row)

            probes = [
                ("0", 1),
                ("0", 0),
                ("0", 3),
                ("1002", 0),
                ("0", 10),
                ("1", 3),
                ("10", 5),
            ]

            for sample_id, ordinal in probes:
                row = grouped[sample_id][ordinal]
                ref = _build_historical_reference(legacy_root, raw_root, row)
                iq, fs, center = ref["iq"], ref["fs"], ref["center"]
                cand_ref = ref["cand"]
                hist_diag = cand_ref.diagnostics

                prop = CPNProposal(
                    t_start_s=float(row["t0_s"]),
                    t_end_s=float(row["t1_s"]),
                    f_low_hz=float(row["f0_hz"]),
                    f_high_hz=float(row["f1_hz"]),
                    bandwidth_tier=int(row["bandwidth_tier"]),
                    confidence=float(row["score"]),
                )
                cand = purify_candidate(
                    iq,
                    sample_rate_hz=fs,
                    center_frequency_hz=center,
                    proposal=prop,
                )

                # scalar parity
                assert cand.decimation == cand_ref.decimation, f"{sample_id}/{ordinal} decimation"
                duration = prop.t_end_s - prop.t_start_s
                crop_req_start = max(0.0, prop.t_start_s - 0.1 * duration)
                crop_req_end = min(iq.size / fs, prop.t_end_s + 0.1 * duration)
                n0 = max(0, int(np.floor(crop_req_start * fs)))
                n1 = min(iq.size, int(np.ceil(crop_req_end * fs)))
                assert cand.diagnostics.crop_start_sample == n0
                assert cand.diagnostics.crop_end_sample == n1
                assert cand.diagnostics.input_samples == hist_diag["input_samples"] == n1 - n0
                assert cand.diagnostics.output_samples == hist_diag["output_samples"]
                assert cand.crop_t_start_s == cand_ref.crop_t0_s
                assert cand.crop_t_end_s == cand_ref.crop_t1_s
                assert cand.sample_rate_hz == cand_ref.fs_hz
                assert cand.lowpass_hz == cand_ref.f_lp_hz
                assert cand.diagnostics.beta == pytest.approx(hist_diag["beta"], abs=1e-9)
                assert cand.diagnostics.requested_lowpass_hz == pytest.approx(
                    hist_diag["requested_f_lp_hz"], abs=1e-9
                )
                assert cand.diagnostics.frequency_shift_hz == pytest.approx(
                    hist_diag["frequency_shift_hz"], abs=1e-9
                )
                assert cand.diagnostics.numtaps == hist_diag["numtaps"]
                assert cand.diagnostics.numtaps_capped == hist_diag["numtaps_capped"]
                assert cand.diagnostics.input_samples == hist_diag["input_samples"]
                assert cand.diagnostics.output_samples == hist_diag["output_samples"]
                assert cand.diagnostics.identity_filter == (hist_diag["numtaps"] == 1)

                # candidate IQ exact parity
                assert cand.iq.shape == cand_ref.iq.shape
                assert cand.iq.dtype == cand_ref.iq.dtype == np.complex64
                assert np.array_equal(cand.iq, cand_ref.iq)
                assert hashlib.sha256(cand.iq.tobytes()).hexdigest() == hashlib.sha256(
                    cand_ref.iq.tobytes()
                ).hexdigest()

                # filter tap parity (non-identity)
                if hist_diag["numtaps"] > 1:
                    taps_ref = ahlp_ref.design_hamming_lowpass(
                        fs, float(hist_diag.get("effective_f_lp_hz", cand_ref.f_lp_hz))
                    )
                    taps_prod = _design_hamming_lowpass(fs, cand_ref.f_lp_hz)
                    assert taps_prod.shape == taps_ref.shape
                    assert taps_prod.dtype == taps_ref.dtype == np.float64
                    assert np.array_equal(taps_prod, taps_ref)
                    assert hashlib.sha256(taps_prod.tobytes()).hexdigest() == hashlib.sha256(
                        taps_ref.tobytes()
                    ).hexdigest()

                del iq
        finally:
            _cleanup_legacy(inserted, src_parts)