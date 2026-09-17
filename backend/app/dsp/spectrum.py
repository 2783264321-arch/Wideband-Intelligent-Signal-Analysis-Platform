"""Bounded averaged-PSD preview for signal inspection.

This is a VISUALIZATION preview, not a frozen benchmark metric. It deliberately
reads only ``segments * min(fft_size, num_samples)`` IQ samples (via the unified
reader), so a multi-GB recording is never fully loaded to draw its spectrum.

NumPy only; no new DSP dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.errors import PlatformError
from app.dsp.iq import read_iq

DEFAULT_FFT_SIZE = 4096
DEFAULT_SEGMENTS = 8
_MIN_FFT_SIZE = 256
_MAX_FFT_SIZE = 16384
_MIN_SEGMENTS = 1
_MAX_SEGMENTS = 32
_POWER_EPSILON = 1e-12


@dataclass(frozen=True)
class SpectrumPreview:
    frequency_hz: list[float]
    power_db: list[float]
    fft_size: int
    segment_count: int


def _window_starts(num_samples: int, window_length: int, segments: int) -> list[int]:
    """Evenly distributed window starts across the whole recording."""
    if num_samples <= window_length:
        return [0]
    span = num_samples - window_length
    if segments <= 1:
        return [span // 2]
    return [round(index * span / (segments - 1)) for index in range(segments)]


def compute_spectrum_preview(
    recording,
    *,
    data_root: Path,
    fft_size: int = DEFAULT_FFT_SIZE,
    segments: int = DEFAULT_SEGMENTS,
) -> SpectrumPreview:
    num_samples = int(recording.num_samples)
    if num_samples <= 0:
        raise PlatformError("INVALID_RECORDING", "Recording has no samples to analyze.")
    fft_size = int(fft_size)
    segments = int(segments)
    if not (_MIN_FFT_SIZE <= fft_size <= _MAX_FFT_SIZE):
        raise PlatformError("INVALID_RECORDING", f"fft_size must be between {_MIN_FFT_SIZE} and {_MAX_FFT_SIZE}.")
    if not (_MIN_SEGMENTS <= segments <= _MAX_SEGMENTS):
        raise PlatformError("INVALID_RECORDING", f"segments must be between {_MIN_SEGMENTS} and {_MAX_SEGMENTS}.")

    window_length = min(fft_size, num_samples)
    starts = _window_starts(num_samples, window_length, segments)
    window = np.hanning(window_length).astype(np.float64)

    accumulated = np.zeros(fft_size, dtype=np.float64)
    used = 0
    for start in starts:
        segment = np.asarray(read_iq(recording, data_root, start, window_length), dtype=np.complex128)
        if segment.size == 0:
            continue
        if segment.size < window_length:
            padded = np.zeros(window_length, dtype=np.complex128)
            padded[: segment.size] = segment
            segment = padded
        transform = np.fft.fftshift(np.fft.fft(segment * window, n=fft_size))
        accumulated += np.abs(transform) ** 2
        used += 1

    if used == 0:
        raise PlatformError("INVALID_RECORDING", "Recording has no readable samples.")

    average_power = accumulated / used
    power_db = 10.0 * np.log10(average_power + _POWER_EPSILON)
    baseband_hz = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / recording.sample_rate_hz))
    frequency_hz = baseband_hz + recording.center_frequency_hz

    return SpectrumPreview(
        frequency_hz=np.asarray(frequency_hz, dtype=float).tolist(),
        power_db=np.asarray(power_db, dtype=float).tolist(),
        fft_size=fft_size,
        segment_count=used,
    )
