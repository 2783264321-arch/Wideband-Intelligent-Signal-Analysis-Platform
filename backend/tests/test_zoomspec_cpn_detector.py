"""Task 12B focused tests for the frozen CPN detector production port.

Every focused test node id contains ``task12b`` so the suite can be selected
with ``-k task12b``.

Pure geometry/contract tests run without GPU and without Ultralytics (they
must pass under ``repo/.venv``). Model and external historical parity tests
skip truthfully where the ML stack / external assets are absent; on the
AutoDL acceptance server they MUST execute.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
    LSSTFTSpectrogram,
    SpectrogramGeometry,
    make_ls_frequency_grid,
)

CHECKPOINT_SHA256 = "eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311"
ORACLE_SHA256 = "021bc47e604303a2711c2b860d681ecc698b2bbcc464c0d10679e1b5e9fd1c79"
APPROVED_PROBES = ["0", "156", "2121", "435", "999"]
EXPECTED_PROBE_COUNTS = {"0": 17, "156": 14, "2121": 22, "435": 21, "999": 24}


def _geometry() -> SpectrogramGeometry:
    freq = make_ls_frequency_grid(2_400_000_000.0, 2_405_000_000.0)
    time = np.linspace(0.0, 0.006, 640)
    return SpectrogramGeometry(
        frequency_grid_hz=freq,
        time_grid_s=time,
        time_extent_s=(0.0, 0.006),
        frequency_extent_hz=(2_400_000_000.0, 2_405_000_000.0),
    )


def _match_proposals(
    baseline: list,
    candidate: list,
) -> list[tuple[int, int]]:
    """Match candidate items to baseline by nearest normalized time center.

    Returns aligned (baseline_index, candidate_index) pairs. Matching uses
    normalized-xyxy space when available; otherwise physical t_start.
    """
    matched = []
    used = set()
    for b_idx, b in enumerate(baseline):
        best_idx = None
        best_dist = float("inf")
        for c_idx, c in enumerate(candidate):
            if c_idx in used:
                continue
            if hasattr(b, "normalized_xyxy") and hasattr(c, "normalized_xyxy"):
                dist = sum(abs(b.normalized_xyxy[j] - c.normalized_xyxy[j]) for j in range(4))
            else:
                dist = abs(b.t_start_s - c.t_start_s)
            if dist < best_dist:
                best_dist = dist
                best_idx = c_idx
        if best_idx is not None:
            used.add(best_idx)
            matched.append((b_idx, best_idx))
    return matched


def _ml_or_skip():
    try:
        import torch  # noqa: F401
        import ultralytics  # noqa: F401
    except ImportError:
        pytest.skip("ML stack unavailable; requires /root/miniconda3/bin/python")
    return True


def _cuda_or_skip():
    _ml_or_skip()
    import torch

    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable; model/parity tests require CUDA")
    return True


class TestTask12bModuleImport:
    def test_task12b_module_imports_without_ml_stack(self):
        import app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector as detector

        assert hasattr(detector, "CPNDetector")
        assert hasattr(detector, "CPNRawDetection")
        assert hasattr(detector, "CPNProposal")


class TestTask12bDataclassContract:
    def test_task12b_cpnrawdetection_contract(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNRawDetection

        det = CPNRawDetection((0.1, 0.2, 0.5, 0.6), 0.9, 1)
        assert det.normalized_xyxy == (0.1, 0.2, 0.5, 0.6)
        assert det.confidence == 0.9
        assert det.bandwidth_tier == 1

    def test_task12b_cpnproposal_contract(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

        p = CPNProposal(0.001, 0.003, 2_401_000_000.0, 2_401_500_000.0, 2, 0.8)
        assert p.t_start_s == 0.001
        assert p.t_end_s == 0.003
        assert p.f_low_hz == 2_401_000_000.0
        assert p.f_high_hz == 2_401_500_000.0
        assert p.bandwidth_tier == 2
        assert p.confidence == 0.8


class TestTask12bInputValidation:
    def test_task12b_accepts_640x640_uint8(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _validate_image

        img = np.zeros((640, 640), dtype=np.uint8)
        _validate_image(img)

    def test_task12b_rejects_wrong_dtype(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _validate_image

        img = np.zeros((640, 640), dtype=np.float32)
        with pytest.raises(ValueError):
            _validate_image(img)

    def test_task12b_rejects_wrong_shape(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _validate_image

        img = np.zeros((128, 128), dtype=np.uint8)
        with pytest.raises(ValueError):
            _validate_image(img)


class TestTask12bGeometry:
    def test_task12b_grid_edges_midpoint_construction(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _grid_edges

        grid = np.array([0.0, 2.0, 4.0, 6.0], dtype=np.float64)
        edges = _grid_edges(grid)
        assert edges[1] == 1.0
        assert edges[2] == 3.0
        assert edges[3] == 5.0
        assert edges[0] == -1.0
        assert edges[4] == 7.0

    def test_task12b_grid_edges_extent_override(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _grid_edges

        grid = np.array([0.0, 2.0, 4.0, 6.0], dtype=np.float64)
        edges = _grid_edges(grid, extent=(0.0, 6.0))
        assert edges[0] == 0.0
        assert edges[4] == 6.0

    def test_task12b_full_frame_box_maps_to_extents(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        p = image_box_to_cpn_proposal((0.0, 0.0, 1.0, 1.0), g, bandwidth_tier=1, confidence=0.9)
        assert p.t_start_s == pytest.approx(0.0)
        assert p.t_end_s == pytest.approx(0.006)
        assert p.f_low_hz == pytest.approx(2_400_000_000.0)
        assert p.f_high_hz == pytest.approx(2_405_000_000.0)

    def test_task12b_interior_box(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        p = image_box_to_cpn_proposal((0.25, 0.25, 0.75, 0.75), g, bandwidth_tier=0, confidence=0.5)
        assert p.t_start_s > 0.0
        assert p.t_end_s < 0.006
        assert p.f_low_hz > 2_400_000_000.0
        assert p.f_high_hz < 2_405_000_000.0

    def test_task12b_x0_boundary(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        p = image_box_to_cpn_proposal((0.0, 0.3, 0.5, 0.8), g, bandwidth_tier=1, confidence=0.5)
        assert p.t_start_s == pytest.approx(0.0)

    def test_task12b_x1_boundary(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        p = image_box_to_cpn_proposal((0.5, 0.3, 1.0, 0.8), g, bandwidth_tier=1, confidence=0.5)
        assert p.t_end_s == pytest.approx(0.006)

    def test_task12b_y0_boundary(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        p = image_box_to_cpn_proposal((0.3, 0.0, 0.8, 0.5), g, bandwidth_tier=1, confidence=0.5)
        assert p.f_high_hz == pytest.approx(2_405_000_000.0)

    def test_task12b_y1_boundary(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        p = image_box_to_cpn_proposal((0.3, 0.5, 0.8, 1.0), g, bandwidth_tier=1, confidence=0.5)
        assert p.f_low_hz == pytest.approx(2_400_000_000.0)

    def test_task12b_y_down_inversion(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import image_box_to_cpn_proposal

        g = _geometry()
        high_box = image_box_to_cpn_proposal((0.2, 0.1, 0.4, 0.3), g, bandwidth_tier=1, confidence=0.5)
        low_box = image_box_to_cpn_proposal((0.2, 0.7, 0.4, 0.9), g, bandwidth_tier=1, confidence=0.5)
        assert high_box.f_low_hz > low_box.f_low_hz

    def test_task12b_clip_and_reject(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _clip_and_reject

        clipped = _clip_and_reject((0.1, 0.1, 0.9, 0.9))
        assert clipped == (0.1, 0.1, 0.9, 0.9)
        assert _clip_and_reject((-0.1, 0.1, 1.1, 0.9)) == (0.0, 0.1, 1.0, 0.9)
        assert _clip_and_reject((0.5, 0.5, 0.5, 0.9)) is None
        assert _clip_and_reject((0.5, 0.5, 0.9, 0.5)) is None


class TestTask12bModel:
    def test_task12b_model_loads_once_and_detects(self):
        _cuda_or_skip()
        ckpt = os.environ.get("WSP_TASK12B_CHECKPOINT_PATH")
        if not ckpt:
            pytest.skip("WSP_TASK12B_CHECKPOINT_PATH not set")
        if hashlib.sha256(Path(ckpt).read_bytes()).hexdigest() != CHECKPOINT_SHA256:
            raise AssertionError("checkpoint SHA mismatch")

        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNDetector
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import LSSTFTNormalization

        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        spec = LSSTFTSpectrogram(
            image=np.zeros((640, 640), dtype=np.uint8),
            geometry=_geometry(),
        )
        detector = CPNDetector(Path(ckpt), device=0)
        results = detector.detect_batch([spec], batch_size=1)
        assert isinstance(results, list)
        assert len(results) == 1
        for p in results[0]:
            assert isinstance(p, tuple) or hasattr(p, "t_start_s")

    def test_task12b_bandwidth_tier_ids(self):
        _cuda_or_skip()
        ckpt = os.environ.get("WSP_TASK12B_CHECKPOINT_PATH")
        if not ckpt:
            pytest.skip("WSP_TASK12B_CHECKPOINT_PATH not set")
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNDetector, _detect_raw_batch

        spec = LSSTFTSpectrogram(image=np.zeros((640, 640), dtype=np.uint8), geometry=_geometry())
        raw = _detect_raw_batch(CPNDetector(Path(ckpt), device=0), [spec], batch_size=1)
        assert set(r.bandwidth_tier for r in raw[0]) <= {0, 1, 2}


@pytest.mark.skipif(
    not all(
        os.environ.get(v)
        for v in ("WSP_TASK12B_CHECKPOINT_PATH", "WSP_TASK12B_CPN_ORACLE",
                  "WSP_TASK12B_CACHE_ROOT", "WSP_TASK12B_RAW_TEST_ROOT",
                  "WSP_TASK12B_NORMALIZATION_PATH", "WSP_TASK12B_TEST_MANIFEST")
    ),
    reason="Task-12B external historical assets not supplied via WSP_TASK12B_* env",
)
class TestTask12bHistoricalParity:
    def test_task12b_historical_five_probe_cpn_parity(self):
        _cuda_or_skip()
        from PIL import Image

        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNDetector
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
            LSSTFTNormalization,
            build_ls_stft_spectrogram,
        )
        from app.recordings.reader import read_segment_from_path

        ckpt = Path(os.environ["WSP_TASK12B_CHECKPOINT_PATH"])
        oracle_path = Path(os.environ["WSP_TASK12B_CPN_ORACLE"])
        cache_root = Path(os.environ["WSP_TASK12B_CACHE_ROOT"])
        raw_root = Path(os.environ["WSP_TASK12B_RAW_TEST_ROOT"])
        norm_path = Path(os.environ["WSP_TASK12B_NORMALIZATION_PATH"])
        manifest_path = Path(os.environ["WSP_TASK12B_TEST_MANIFEST"])

        assert hashlib.sha256(ckpt.read_bytes()).hexdigest() == CHECKPOINT_SHA256
        assert hashlib.sha256(oracle_path.read_bytes()).hexdigest() == ORACLE_SHA256

        test_ids = json.loads(manifest_path.read_text(encoding="utf-8"))["test_ids"]
        norm_raw = json.loads(norm_path.read_text(encoding="utf-8"))
        norm = LSSTFTNormalization(
            percentile_low=float(norm_raw["percentile_low"]),
            percentile_high=float(norm_raw["percentile_high"]),
            value_low=float(norm_raw["value_low"]),
            value_high=float(norm_raw["value_high"]),
        )

        oracle_rows = [json.loads(l) for l in oracle_path.read_text().splitlines()]
        oracle = {}
        for row in oracle_rows:
            oracle.setdefault(row["sample_id"], []).append(row)

        detector = CPNDetector(ckpt, device=0)
        batch_size = 16
        for stem in APPROVED_PROBES:
            idx = test_ids.index(stem)
            chunk_start = (idx // batch_size) * batch_size
            chunk_ids = test_ids[chunk_start: chunk_start + batch_size]
            pos = idx - chunk_start

            spectrograms = []
            for cid in chunk_ids:
                if cid == stem:
                    meta = json.loads((raw_root / f"{cid}.json").read_text())
                    f_lo, f_hi = (float(x) * 1e6 for x in meta["observation_range"])
                    fs = f_hi - f_lo
                    iq = read_segment_from_path(raw_root / f"{cid}.bin", "float16_interleaved_le")
                    spec = build_ls_stft_spectrogram(
                        iq,
                        sample_rate_hz=fs,
                        center_frequency_hz=0.5 * (f_lo + f_hi),
                        frequency_low_hz=f_lo,
                        frequency_high_hz=f_hi,
                        normalization=norm,
                        device="cuda",
                    )
                    del iq
                else:
                    with Image.open(cache_root / "images" / "test" / f"{cid}.png") as im:
                        pixels = np.asarray(im, dtype=np.uint8)
                    spec = LSSTFTSpectrogram(image=pixels, geometry=_geometry())
                spectrograms.append(spec)

            results = detector.detect_batch(spectrograms, batch_size=batch_size)
            target_proposals = results[pos]
            expected = oracle[stem]
            assert len(target_proposals) == len(expected) == EXPECTED_PROBE_COUNTS[stem]
            for p, o in zip(target_proposals, expected):
                assert p.t_start_s == o["t0_s"]
                assert p.t_end_s == o["t1_s"]
                assert p.f_low_hz == o["f0_hz"]
                assert p.f_high_hz == o["f1_hz"]
                assert p.bandwidth_tier == o["bandwidth_tier"]
                assert p.confidence == o["score"]


@pytest.mark.skipif(
    not all(
        os.environ.get(v)
        for v in ("WSP_TASK12B_CHECKPOINT_PATH", "WSP_TASK12B_CACHE_ROOT",
                  "WSP_TASK12B_RAW_TEST_ROOT", "WSP_TASK12B_NORMALIZATION_PATH",
                  "WSP_TASK12B_TEST_MANIFEST")
    ),
    reason="Task-12B live batch=1 assets not supplied",
)
class TestTask12bLiveBatch1:
    def test_task12b_live_batch1_vs_historical_batch16(self):
        _cuda_or_skip()
        from PIL import Image

        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import (
            CPNDetector,
            _detect_raw_batch,
        )
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
            LSSTFTNormalization,
            build_ls_stft_spectrogram,
        )
        from app.recordings.reader import read_segment_from_path

        ckpt = Path(os.environ["WSP_TASK12B_CHECKPOINT_PATH"])
        cache_root = Path(os.environ["WSP_TASK12B_CACHE_ROOT"])
        raw_root = Path(os.environ["WSP_TASK12B_RAW_TEST_ROOT"])
        norm_path = Path(os.environ["WSP_TASK12B_NORMALIZATION_PATH"])
        manifest_path = Path(os.environ["WSP_TASK12B_TEST_MANIFEST"])

        test_ids = json.loads(manifest_path.read_text(encoding="utf-8"))["test_ids"]
        norm_raw = json.loads(norm_path.read_text(encoding="utf-8"))
        norm = LSSTFTNormalization(
            percentile_low=float(norm_raw["percentile_low"]),
            percentile_high=float(norm_raw["percentile_high"]),
            value_low=float(norm_raw["value_low"]),
            value_high=float(norm_raw["value_high"]),
        )
        detector = CPNDetector(ckpt, device=0)
        batch_size = 16
        for stem in APPROVED_PROBES:
            idx = test_ids.index(stem)
            chunk_start = (idx // batch_size) * batch_size
            chunk_ids = test_ids[chunk_start: chunk_start + batch_size]
            pos = idx - chunk_start

            meta = json.loads((raw_root / f"{stem}.json").read_text())
            f_lo, f_hi = (float(x) * 1e6 for x in meta["observation_range"])
            fs = f_hi - f_lo
            iq = read_segment_from_path(raw_root / f"{stem}.bin", "float16_interleaved_le")
            target_spec = build_ls_stft_spectrogram(
                iq,
                sample_rate_hz=fs,
                center_frequency_hz=0.5 * (f_lo + f_hi),
                frequency_low_hz=f_lo,
                frequency_high_hz=f_hi,
                normalization=norm,
                device="cuda",
            )
            del iq

            chunk_specs = []
            for cid in chunk_ids:
                if cid == stem:
                    chunk_specs.append(target_spec)
                else:
                    with Image.open(cache_root / "images" / "test" / f"{cid}.png") as im:
                        pixels = np.asarray(im, dtype=np.uint8)
                    chunk_specs.append(LSSTFTSpectrogram(image=pixels, geometry=_geometry()))

            raw_batch16 = _detect_raw_batch(detector, chunk_specs, batch_size=batch_size)[pos]
            raw_live1 = _detect_raw_batch(detector, [target_spec], batch_size=1)[0]

            assert len(raw_batch16) == len(raw_live1)
            tiers_b = sorted(r.bandwidth_tier for r in raw_batch16)
            tiers_l = sorted(r.bandwidth_tier for r in raw_live1)
            assert tiers_b == tiers_l

            proposals_batch16 = detector.detect_batch(chunk_specs, batch_size=batch_size)[pos]
            proposals_live1 = detector.detect_batch([target_spec], batch_size=1)[0]
            assert len(proposals_batch16) == len(proposals_live1)
            matched_idx = _match_proposals(raw_batch16, raw_live1)
            assert len(matched_idx) == len(raw_batch16)
            max_xyxy = 0.0
            max_conf = 0.0
            max_t_start = 0.0
            max_t_end = 0.0
            max_f_low = 0.0
            max_f_high = 0.0
            for b_idx, l_idx in matched_idx:
                rb = raw_batch16[b_idx]
                rl = raw_live1[l_idx]
                for j in range(4):
                    max_xyxy = max(max_xyxy, abs(rb.normalized_xyxy[j] - rl.normalized_xyxy[j]))
                max_conf = max(max_conf, abs(rb.confidence - rl.confidence))
                pb = proposals_batch16[b_idx]
                pl = proposals_live1[l_idx]
                max_t_start = max(max_t_start, abs(pb.t_start_s - pl.t_start_s))
                max_t_end = max(max_t_end, abs(pb.t_end_s - pl.t_end_s))
                max_f_low = max(max_f_low, abs(pb.f_low_hz - pl.f_low_hz))
                max_f_high = max(max_f_high, abs(pb.f_high_hz - pl.f_high_hz))

            evidence = {
                "probe": stem,
                "count_equal": True,
                "tiers_equal": True,
                "max_xyxy_diff": max_xyxy,
                "max_conf_diff": max_conf,
                "max_t_start_diff_s": max_t_start,
                "max_t_end_diff_s": max_t_end,
                "max_f_low_diff_hz": max_f_low,
                "max_f_high_diff_hz": max_f_high,
            }
            print(f"TASK12B_LIVE_DRIFT {json.dumps(evidence)}")


class _FakeBoxes:
    def __init__(self, count: int):
        self.xyxyn = np.zeros((count, 4), dtype=np.float32)
        self.conf = np.zeros(count, dtype=np.float32)
        self.cls = np.zeros(count, dtype=np.float32)


class _FakeResult:
    def __init__(self, boxes: _FakeBoxes | None):
        self.boxes = boxes


class _FakeModel:
    """Configurable fake Ultralytics model for fail-closed boundary tests."""

    def __init__(self, results_per_call: list[int] | int):
        if isinstance(results_per_call, int):
            results_per_call = [results_per_call]
        self._results_per_call = list(results_per_call)
        self._call = 0
        self.predict_calls = []

    def predict(self, source, **kwargs):
        call_index = min(self._call, len(self._results_per_call) - 1)
        self._call += 1
        self.predict_calls.append({"source_len": len(source), "kwargs": kwargs})
        n = self._results_per_call[call_index]
        return [_FakeResult(None) for _ in range(n)]


class _FakeDetector:
    def __init__(self, model: _FakeModel):
        self._model = model
        self._device = 0


def _fake_spec() -> LSSTFTSpectrogram:
    return LSSTFTSpectrogram(image=np.zeros((640, 640), dtype=np.uint8), geometry=_geometry())


class TestTask12bFailClosedBatch:
    def test_task12b_batch_size_zero_raises_valueerror(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _detect_raw_batch

        detector = _FakeDetector(_FakeModel(1))
        with pytest.raises(ValueError):
            _detect_raw_batch(detector, [_fake_spec()], batch_size=0)

    def test_task12b_batch_size_negative_raises_valueerror(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _detect_raw_batch

        detector = _FakeDetector(_FakeModel(1))
        with pytest.raises(ValueError):
            _detect_raw_batch(detector, [_fake_spec()], batch_size=-1)

    def test_task12b_short_result_count_raises_runtimeerror(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _detect_raw_batch

        model = _FakeModel(results_per_call=[1])
        detector = _FakeDetector(model)
        specs = [_fake_spec(), _fake_spec()]
        with pytest.raises(RuntimeError):
            _detect_raw_batch(detector, specs, batch_size=2)

    def test_task12b_excess_result_count_raises_runtimeerror(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _detect_raw_batch

        model = _FakeModel(results_per_call=[2])
        detector = _FakeDetector(model)
        specs = [_fake_spec()]
        with pytest.raises(RuntimeError):
            _detect_raw_batch(detector, specs, batch_size=1)

    def test_task12b_total_cardinality_mismatch_raises_runtimeerror(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import _detect_raw_batch

        model = _FakeModel(results_per_call=[1, 2])
        detector = _FakeDetector(model)
        specs = [_fake_spec(), _fake_spec(), _fake_spec()]
        with pytest.raises(RuntimeError):
            _detect_raw_batch(detector, specs, batch_size=1)

    def test_task12b_no_physical_conversion_after_cardinality_mismatch(self):
        from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import (
            _detect_raw_batch,
            image_box_to_cpn_proposal,
        )

        original = image_box_to_cpn_proposal
        calls = []

        def _spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        import app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector as detector_module

        detector_module.image_box_to_cpn_proposal = _spy
        try:
            model = _FakeModel(results_per_call=[1])
            detector = _FakeDetector(model)
            specs = [_fake_spec(), _fake_spec()]
            with pytest.raises(RuntimeError):
                _detect_raw_batch(detector, specs, batch_size=2)
            assert not calls
        finally:
            detector_module.image_box_to_cpn_proposal = original