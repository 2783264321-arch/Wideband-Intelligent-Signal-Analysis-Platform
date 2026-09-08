"""Platform-native Torch LS-STFT preprocessing (Task 12A).

Reproduces the historical frozen ZoomSpec LS-STFT detector-input behavior
(``spectral_torch.py :: build_spectrogram_torch`` + ``build_cpn_dataset.py``
final image construction) as an independent production implementation.

Task 12A Gate 0 classified the historical frozen TEST-cache backend as
``TORCH_BEHAVIOR_PARITY_CONFIRMED``; this module is the Task 12A parity
reference and must NOT silently fall back to a NumPy STFT implementation.

Independence contract:
- The legacy ZoomSpec source package is never imported.
- No historical ``/root/autodl-tmp/...`` asset paths.
- The interpreter search path is never mutated.
- Torch is imported lazily inside the numerical path only, so ordinary
  backend modules remain importable in environments without Torch (e.g.
  ``repo/.venv``), while actual LS-STFT execution requires Torch.
- No NumPy STFT fallback.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LSSTFTNormalization:
    percentile_low: float
    percentile_high: float
    value_low: float
    value_high: float


@dataclass(frozen=True)
class SpectrogramGeometry:
    frequency_grid_hz: np.ndarray
    time_grid_s: np.ndarray
    time_extent_s: tuple[float, float]
    frequency_extent_hz: tuple[float, float]


@dataclass(frozen=True)
class LSSTFTSpectrogram:
    image: np.ndarray
    geometry: SpectrogramGeometry


# Frozen historical parameters. Not exposed as ordinary caller parameters; a
# future experiment with different values is a different pipeline/version.
_REPRESENTATION = "ls_stft"
_MODE = "paper_strict"
_N_FFT = 2048
_WIN_LENGTH = 2048
_HOP_LENGTH = 1024
_OUTPUT_FREQUENCY_BINS = 640
_OUTPUT_TIME_BINS = 640
_EPSILON = 1e-8
_SUBBAND_HZ = 1e6
_ALPHA1 = 1.0
_ALPHA2 = 4.0


def _allocate_even_bins(total_bins: int, n_subbands: int) -> np.ndarray:
    if total_bins < 2 * n_subbands or total_bins % 2:
        raise ValueError("need an even total and at least two bins per subband")
    base = (total_bins // n_subbands) // 2 * 2
    counts = np.full(n_subbands, base, dtype=np.int64)
    extras = (total_bins - int(counts.sum())) // 2
    if extras:
        positions = np.floor((np.arange(extras) + 0.5) * n_subbands / extras).astype(int)
        counts[positions] += 2
    if int(counts.sum()) != total_bins or np.any(counts % 2):
        raise AssertionError("even largest-remainder allocation failed")
    return counts


def make_ls_frequency_grid(
    f_lo_hz: float,
    f_hi_hz: float,
) -> np.ndarray:
    """Paper equations (8)-(12) LS frequency grid with ULP monotonic repair.

    Matches the historical shared helper semantics used by the Torch path
    (``make_ls_frequency_grid``) for ``paper_strict`` mode.
    """
    width = f_hi_hz - f_lo_hz
    n_subbands = int(round(width / _SUBBAND_HZ))
    if n_subbands < 1 or not np.isclose(width, n_subbands * _SUBBAND_HZ, atol=1.0):
        raise ValueError("observation bandwidth must be an integer number of subbands")
    counts = _allocate_even_bins(_OUTPUT_FREQUENCY_BINS, n_subbands)
    parts: list[np.ndarray] = []
    for k, count in enumerate(counts):
        half = count // 2
        delta = (_ALPHA2 - _ALPHA1) / (half - 1) if half > 1 else 0.0
        i = np.arange(half, dtype=np.float64)
        b = (10.0 ** (_ALPHA1 + i * delta) - 10.0 ** _ALPHA1) / (
            10.0 ** _ALPHA2 - 10.0 ** _ALPHA1
        )
        template = np.concatenate((0.5 * b, 1.0 - 0.5 * b[::-1]))
        parts.append(f_lo_hz + k * _SUBBAND_HZ + template * _SUBBAND_HZ)
    grid = np.concatenate(parts)
    for index in range(1, grid.size):
        if grid[index] <= grid[index - 1]:
            grid[index] = np.nextafter(grid[index - 1], np.inf)
    if grid[-1] > f_hi_hz + 1.0:
        raise ValueError("monotonic repair escaped observation range")
    return grid


def _percentile_normalize(values: np.ndarray, low: float, high: float) -> np.ndarray:
    if not low < high:
        raise ValueError("low percentile value must be less than high")
    result = (values - low) / (high - low)
    return np.clip(result, 0.0, 1.0)


def _quantize_uint8(normalized: np.ndarray) -> np.ndarray:
    return np.round(normalized * 255.0).astype(np.uint8)


def build_ls_stft_spectrogram(
    iq: np.ndarray,
    *,
    sample_rate_hz: float,
    center_frequency_hz: float,
    frequency_low_hz: float,
    frequency_high_hz: float,
    normalization: LSSTFTNormalization,
    device: str,
) -> LSSTFTSpectrogram:
    """Build the frozen historical LS-STFT 640x640 uint8 detector image.

    The numerical path is the historical Torch behavior
    (``build_spectrogram_torch``) followed by the historical builder's final
    image construction: percentile normalize -> round(*255) -> uint8 ->
    vertical flip. Torch is imported lazily; without Torch this fails clearly.
    """
    import torch
    import torch.nn.functional as functional

    if iq.ndim != 1:
        raise ValueError("iq must be a one-dimensional complex IQ array")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    target_device = torch.device(device)
    if target_device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA backend requested but torch.cuda.is_available() is false")

    complex_iq = iq.astype(np.complex64, copy=False)
    if complex_iq.size < _WIN_LENGTH:
        complex_iq = np.pad(complex_iq, (0, _WIN_LENGTH - complex_iq.size))
    signal = torch.from_numpy(complex_iq).to(target_device)

    window = torch.hann_window(
        _WIN_LENGTH, periodic=False, dtype=torch.float32, device=target_device
    )
    spectrum = torch.stft(
        signal,
        n_fft=_N_FFT,
        hop_length=_HOP_LENGTH,
        win_length=_WIN_LENGTH,
        window=window,
        center=False,
        onesided=False,
        return_complex=True,
    )
    spectrum = torch.fft.fftshift(spectrum, dim=0)

    source_baseband = np.fft.fftshift(np.fft.fftfreq(_N_FFT, d=1.0 / sample_rate_hz))
    frequency_grid = make_ls_frequency_grid(frequency_low_hz, frequency_high_hz)

    source_frequency = torch.from_numpy(source_baseband).to(target_device)
    target_baseband = torch.from_numpy(frequency_grid - center_frequency_hz).to(target_device)
    upper = torch.searchsorted(source_frequency, target_baseband)
    upper = upper.clamp(1, source_frequency.numel() - 1)
    lower = upper - 1
    denominator = source_frequency[upper] - source_frequency[lower]
    weight = ((target_baseband - source_frequency[lower]) / denominator).clamp(0.0, 1.0)
    weight = weight.to(torch.float32).unsqueeze(1)
    resampled = spectrum[lower] * (1.0 - weight) + spectrum[upper] * weight
    log_magnitude = torch.log(torch.abs(resampled) + _EPSILON)

    values = functional.interpolate(
        log_magnitude[None, None],
        size=(_OUTPUT_FREQUENCY_BINS, _OUTPUT_TIME_BINS),
        mode="bilinear",
        align_corners=True,
    )[0, 0]

    frame_count = int(spectrum.shape[1])
    time_s = (
        np.arange(frame_count, dtype=np.float64) * _HOP_LENGTH + 0.5 * (_WIN_LENGTH - 1)
    ) / sample_rate_hz
    target_time = np.linspace(float(time_s[0]), float(time_s[-1]), _OUTPUT_TIME_BINS)

    values_np = values.detach().cpu().numpy().astype(np.float32, copy=False)
    normalized = _percentile_normalize(values_np, normalization.value_low, normalization.value_high)
    image = _quantize_uint8(normalized)
    image = np.flipud(image)
    image = np.ascontiguousarray(image)

    geometry = SpectrogramGeometry(
        frequency_grid_hz=np.asarray(frequency_grid, dtype=np.float64),
        time_grid_s=np.asarray(target_time, dtype=np.float64),
        time_extent_s=(0.0, float(iq.size) / sample_rate_hz),
        frequency_extent_hz=(float(frequency_low_hz), float(frequency_high_hz)),
    )
    return LSSTFTSpectrogram(image=image, geometry=geometry)