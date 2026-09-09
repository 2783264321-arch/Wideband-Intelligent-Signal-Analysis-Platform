"""Task 12A focused tests for the Torch LS-STFT preprocessing port.

Every Task-12A test node id contains ``task12a`` so the focused suite can be
selected with ``-k task12a``.

Torch-requiring tests skip truthfully when torch is unavailable (e.g. under
``repo/.venv``); Task-12A acceptance uses ``/root/miniconda3/bin/python``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
    LSSTFTNormalization,
    LSSTFTSpectrogram,
    SpectrogramGeometry,
    _percentile_normalize,
    _quantize_uint8,
    build_ls_stft_spectrogram,
)

HISTORICAL_NORMALIZATION_SHA256 = "9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f"
APPROVED_PARITY_STEMS = ["0", "156", "2121", "435", "999"]


def _torch_or_skip():
    try:
        import torch  # noqa: F401
    except ImportError:
        pytest.skip("torch is unavailable; requires /root/miniconda3/bin/python")
    return True


def _synthetic_iq(
    *,
    fs_hz: float,
    duration_s: float,
    center_frequency_hz: float,
    tone_freq_hz: float | None = None,
    tone_start_s: float = 0.0,
    tone_end_s: float | None = None,
    amplitude: float = 1.0,
    seed: int = 42,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = int(fs_hz * duration_s)
    iq = (rng.standard_normal(total) + 1j * rng.standard_normal(total)).astype(np.complex64)
    if tone_freq_hz is not None:
        t = np.arange(total) / fs_hz
        if tone_end_s is None:
            tone_end_s = duration_s
        mask = (t >= tone_start_s) & (t < tone_end_s)
        baseband = tone_freq_hz - center_frequency_hz
        tone = amplitude * np.exp(2j * np.pi * baseband * t).astype(np.complex64)
        iq = iq.astype(np.complex64)
        iq[mask] += tone[mask].astype(np.complex64)
    return iq


def _tone_peak_row(image: np.ndarray, geometry: SpectrogramGeometry, freq_hz: float) -> tuple[int, float]:
    column_profile = image.astype(np.float64).sum(axis=1)
    peak_row = int(np.argmax(column_profile))
    mapped_freq = float(geometry.frequency_grid_hz[639 - peak_row])
    return peak_row, mapped_freq


class TestTask12aFrozenContract:
    def test_task12a_frozen_parameters_not_overridable(self):
        _torch_or_skip()
        import inspect

        signature = inspect.signature(build_ls_stft_spectrogram)
        params = signature.parameters
        assert "n_fft" not in params
        assert "win_length" not in params
        assert "hop_length" not in params
        assert "representation" not in params
        assert "mode" not in params

    def test_task12a_public_signature(self):
        import inspect

        signature = inspect.signature(build_ls_stft_spectrogram)
        assert list(signature.parameters) == [
            "iq",
            "sample_rate_hz",
            "center_frequency_hz",
            "frequency_low_hz",
            "frequency_high_hz",
            "normalization",
            "device",
        ]
        for name in (
            "sample_rate_hz",
            "center_frequency_hz",
            "frequency_low_hz",
            "frequency_high_hz",
            "normalization",
            "device",
        ):
            assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY

    def test_task12a_normalization_dataclass(self):
        norm = LSSTFTNormalization(
            percentile_low=1.0,
            percentile_high=99.5,
            value_low=0.1356358379125595,
            value_high=6.512740135192871,
        )
        assert norm.percentile_low == 1.0
        assert norm.percentile_high == 99.5
        assert norm.value_low == pytest.approx(0.1356358379125595)
        assert norm.value_high == pytest.approx(6.512740135192871)


class TestTask12aImageContract:
    def test_task12a_output_shape_dtype_range_contiguous(self):
        _torch_or_skip()
        fs = 5_000_000.0
        iq = _synthetic_iq(fs_hz=fs, duration_s=0.006, tone_freq_hz=2_404_000_000.0, center_frequency_hz=2_402_500_000.0)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        assert isinstance(result, LSSTFTSpectrogram)
        assert result.image.shape == (640, 640)
        assert result.image.dtype == np.uint8
        assert result.image.flags["C_CONTIGUOUS"]
        assert result.image.min() >= 0
        assert result.image.max() <= 255

    def test_task12a_zero_input_produces_zero_image(self):
        _torch_or_skip()
        fs = 5_000_000.0
        iq = np.zeros(int(fs * 0.006), dtype=np.complex64)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        assert result.image.shape == (640, 640)
        assert result.image.max() == 0


class TestTask12aNormalizationSemantics:
    def test_task12a_percentile_normalize_clips(self):
        values = np.array([-5.0, 0.1356358379125595, 3.324187986552715, 6.512740135192871, 20.0], dtype=np.float32)
        normalized = _percentile_normalize(values, 0.1356358379125595, 6.512740135192871)
        assert normalized[0] == 0.0
        assert normalized[2] == pytest.approx(0.5, abs=1e-6)
        assert normalized[4] == 1.0

    def test_task12a_quantize_uint8_rounding_boundary(self):
        normalized = np.array([0.0, 0.5 / 255.0, 1.5 / 255.0, 2.5 / 255.0, 1.0], dtype=np.float32)
        image = _quantize_uint8(normalized)
        assert image.dtype == np.uint8
        assert image.tolist() == [0, 0, 2, 2, 255]

    def test_task12a_quantize_uint8_round_not_truncate(self):
        normalized = np.array([0.6 / 255.0, 1.4 / 255.0, 200.5 / 255.0], dtype=np.float32)
        image = _quantize_uint8(normalized)
        assert image.tolist() == [1, 1, 200]


class TestTask12aOrientationAndGeometry:
    def test_task12a_high_frequency_at_top_exactly_one_flip(self):
        _torch_or_skip()
        fs = 5_000_000.0
        iq = _synthetic_iq(fs_hz=fs, duration_s=0.006, tone_freq_hz=2_404_000_000.0, center_frequency_hz=2_402_500_000.0)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        peak_row, mapped_freq = _tone_peak_row(result.image, result.geometry, 2_404_000_000.0)
        assert abs(mapped_freq - 2_404_000_000.0) < 20_000.0
        assert peak_row < 320

    def test_task12a_low_frequency_at_bottom(self):
        _torch_or_skip()
        fs = 5_000_000.0
        iq = _synthetic_iq(fs_hz=fs, duration_s=0.006, tone_freq_hz=2_401_000_000.0, center_frequency_hz=2_402_500_000.0)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        peak_row, mapped_freq = _tone_peak_row(result.image, result.geometry, 2_401_000_000.0)
        assert abs(mapped_freq - 2_401_000_000.0) < 20_000.0
        assert peak_row >= 320

    def test_task12a_frequency_grid_is_ascending_nonlinear(self):
        _torch_or_skip()
        fs = 5_000_000.0
        iq = _synthetic_iq(fs_hz=fs, duration_s=0.006, center_frequency_hz=2_402_500_000.0)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        grid = result.geometry.frequency_grid_hz
        assert grid.shape == (640,)
        assert np.all(np.diff(grid) > 0)
        assert grid[0] >= 2_400_000_000.0
        assert grid[-1] <= 2_405_000_000.0
        steps = np.diff(grid)
        assert np.max(steps) - np.min(steps) > 1.0

    def test_task12a_time_grid_left_to_right(self):
        _torch_or_skip()
        fs = 5_000_000.0
        duration = 0.006
        iq = _synthetic_iq(fs_hz=fs, duration_s=duration, tone_freq_hz=2_403_000_000.0, center_frequency_hz=2_402_500_000.0,
                           tone_start_s=duration * 0.5, tone_end_s=duration)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        time_grid = result.geometry.time_grid_s
        assert time_grid.shape == (640,)
        assert np.all(np.diff(time_grid) > 0)
        half = time_grid.shape[0] // 2
        right_energy = result.image[:, half:].astype(np.float64).sum()
        left_energy = result.image[:, :half].astype(np.float64).sum()
        assert right_energy > left_energy

    def test_task12a_geometry_extents(self):
        _torch_or_skip()
        fs = 5_000_000.0
        duration = 0.006
        iq = _synthetic_iq(fs_hz=fs, duration_s=duration, center_frequency_hz=2_402_500_000.0)
        norm = LSSTFTNormalization(1.0, 99.5, 0.1356358379125595, 6.512740135192871)
        result = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=fs,
            center_frequency_hz=2_402_500_000.0,
            frequency_low_hz=2_400_000_000.0,
            frequency_high_hz=2_405_000_000.0,
            normalization=norm,
            device="cpu",
        )
        assert result.geometry.time_extent_s == (0.0, pytest.approx(duration, abs=1e-9))
        assert result.geometry.frequency_extent_hz == (2_400_000_000.0, 2_405_000_000.0)


class TestTask12aProductionIndependence:
    def test_task12a_production_independence_guard(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "pipelines" / "zoomspec_yolo26n_aug_combined_frn_v3" / "preprocessing.py"
        source = source_path.read_text(encoding="utf-8")
        forbidden = [
            "zoomspec_repro",
            "/root/autodl-tmp/ZoomSpec",
            "/root/autodl-tmp/Claude",
            "sys.path",
            "import subprocess",
            "from subprocess",
            "np.stft",
            "scipy.signal.stft",
            "scipy.stft",
            "Image.open",
            "cv2",
        ]
        for token in forbidden:
            assert token not in source, f"forbidden token present: {token}"

    def test_task12a_no_legacy_runtime_import(self):
        import sys as _sys

        production_module = "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing"
        assert production_module in _sys.modules
        for name in _sys.modules:
            if "zoomspec_repro" in name or "/root/autodl-tmp/ZoomSpec" in name:
                raise AssertionError(f"legacy module loaded: {name}")


@pytest.mark.skipif(
    not (os.environ.get("WSP_TASK12A_CACHE_ROOT") and os.environ.get("WSP_TASK12A_RAW_TEST_ROOT")),
    reason="historical parity roots not supplied via WSP_TASK12A_* environment variables",
)
class TestTask12aHistoricalParity:
    def test_task12a_historical_five_sample_pixel_parity(self):
        _torch_or_skip()
        import torch

        if not torch.cuda.is_available():
            pytest.skip("CUDA unavailable; historical parity requires CUDA")
        from PIL import Image

        from app.recordings.reader import read_segment_from_path

        cache_root = Path(os.environ["WSP_TASK12A_CACHE_ROOT"])
        raw_root = Path(os.environ["WSP_TASK12A_RAW_TEST_ROOT"])
        normalization_path = Path(os.environ["WSP_TASK12A_NORMALIZATION_PATH"])

        normalization_raw = json.loads(normalization_path.read_text(encoding="utf-8"))
        assert hashlib.sha256(normalization_path.read_bytes()).hexdigest() == HISTORICAL_NORMALIZATION_SHA256
        norm = LSSTFTNormalization(
            percentile_low=float(normalization_raw["percentile_low"]),
            percentile_high=float(normalization_raw["percentile_high"]),
            value_low=float(normalization_raw["value_low"]),
            value_high=float(normalization_raw["value_high"]),
        )

        all_pass = True
        failures = []
        for stem in APPROVED_PARITY_STEMS:
            historical_png = cache_root / "images" / "test" / f"{stem}.png"
            bin_path = raw_root / f"{stem}.bin"
            json_path = raw_root / f"{stem}.json"
            if not (historical_png.is_file() and bin_path.is_file() and json_path.is_file()):
                failures.append({"stem": stem, "error": "missing historical inputs"})
                all_pass = False
                continue

            iq = read_segment_from_path(bin_path, "float16_interleaved_le")
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            f_lo_hz, f_hi_hz = (float(x) * 1e6 for x in meta["observation_range"])
            sample_rate_hz = f_hi_hz - f_lo_hz
            center_frequency_hz = 0.5 * (f_lo_hz + f_hi_hz)

            result = build_ls_stft_spectrogram(
                iq,
                sample_rate_hz=sample_rate_hz,
                center_frequency_hz=center_frequency_hz,
                frequency_low_hz=f_lo_hz,
                frequency_high_hz=f_hi_hz,
                normalization=norm,
                device="cuda",
            )
            del iq

            with Image.open(historical_png) as im:
                historical_pixels = np.asarray(im, dtype=np.uint8)

            production_pixels = result.image
            assert production_pixels.shape == historical_pixels.shape
            diff = production_pixels.astype(np.int16) - historical_pixels.astype(np.int16)
            n_diff = int(np.count_nonzero(diff))
            total = diff.size
            identical_fraction = (total - n_diff) / total
            max_abs_diff = int(np.abs(diff).max()) if n_diff else 0
            mean_abs_diff = float(np.abs(diff).mean())
            hist_sha = hashlib.sha256(historical_pixels.tobytes()).hexdigest()
            prod_sha = hashlib.sha256(production_pixels.tobytes()).hexdigest()

            row = {
                "stem": stem,
                "hist_shape": list(historical_pixels.shape),
                "cand_shape": list(production_pixels.shape),
                "hist_dtype": str(historical_pixels.dtype),
                "cand_dtype": str(production_pixels.dtype),
                "diff_pixels": n_diff,
                "identical_fraction": identical_fraction,
                "max_abs_diff": max_abs_diff,
                "mean_abs_diff": mean_abs_diff,
                "hist_sha": hist_sha,
                "prod_sha": prod_sha,
            }
            if n_diff != 0:
                all_pass = False
                failures.append(row)
            assert n_diff == 0, f"stem={stem} diff_pixels={n_diff} max_abs={max_abs_diff}"
            assert hist_sha == prod_sha, f"stem={stem} pixel SHA mismatch"

        assert all_pass, f"parity failures: {failures}"