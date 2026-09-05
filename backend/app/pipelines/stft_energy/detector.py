"""CPU-only STFT energy detector: segmentation of wideband IQ into regions.

Detection/localization only. It never assigns a SpaceNet modulation class; the
output semantic label is the single generic ``signal_presence_v1`` class.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_closing, find_objects, label
from scipy.signal import stft as scipy_stft


@dataclass(frozen=True)
class EnergyRegion:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    confidence: float
    energy_margin_db: float


def detect_stft_energy(
    iq: np.ndarray,
    sample_rate_hz: float,
    center_frequency_hz: float,
    *,
    nperseg: int = 512,
    noverlap: int = 256,
    nfft: int = 512,
    noise_floor_percentile: float = 50.0,
    threshold_margin_db: float = 12.0,
    closing_size: int = 5,
    min_area: int = 100,
    min_duration_s: float = 0.0,
    min_bandwidth_hz: float = 0.0,
) -> list[EnergyRegion]:
    if iq.ndim != 1 or iq.size == 0:
        raise ValueError("IQ input must be a non-empty 1D array.")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive.")

    effective_nperseg = min(nperseg, iq.size)
    effective_noverlap = min(noverlap, max(effective_nperseg - 1, 0))
    effective_nfft = max(nfft, effective_nperseg)

    frequencies, times, zxx = scipy_stft(
        iq,
        fs=sample_rate_hz,
        nperseg=effective_nperseg,
        noverlap=effective_noverlap,
        nfft=effective_nfft,
        return_onesided=False,
        boundary=None,
        padded=False,
    )
    frequencies = np.fft.fftshift(frequencies)
    zxx = np.fft.fftshift(zxx, axes=0)
    power_db = 20.0 * np.log10(np.maximum(np.abs(zxx), np.finfo(np.float32).eps))
    time_axis_s = np.asarray(times, dtype=np.float64)
    frequency_axis_hz = np.asarray(frequencies + center_frequency_hz, dtype=np.float64)

    noise_floor_db = float(np.percentile(power_db, noise_floor_percentile))
    threshold_db = noise_floor_db + threshold_margin_db
    mask = power_db > threshold_db

    if closing_size > 1:
        structure = np.ones((closing_size, closing_size), dtype=bool)
        mask = binary_closing(mask, structure=structure)

    labels_array, _ = label(mask)
    time_bin_hz = float(frequency_axis_hz[1] - frequency_axis_hz[0])
    time_hop_s = float(time_axis_s[1] - time_axis_s[0])

    regions: list[EnergyRegion] = []
    for component_id, component_slice in enumerate(find_objects(labels_array), start=1):
        if component_slice is None:
            continue
        frequency_slice, time_slice = component_slice
        sub_labels = labels_array[component_slice]
        component_mask = sub_labels == component_id
        rows, cols = np.nonzero(component_mask)
        if len(rows) < min_area:
            continue
        frequency_indices = frequency_slice.start + rows
        time_indices = time_slice.start + cols
        t_low_s = time_axis_s[time_indices.min()] - time_hop_s / 2.0
        t_high_s = time_axis_s[time_indices.max()] + time_hop_s / 2.0
        f_low_hz = frequency_axis_hz[frequency_indices.min()] - time_bin_hz / 2.0
        f_high_hz = frequency_axis_hz[frequency_indices.max()] + time_bin_hz / 2.0
        if t_high_s - t_low_s < min_duration_s:
            continue
        if f_high_hz - f_low_hz < min_bandwidth_hz:
            continue
        component_db = float(np.percentile(power_db[component_slice][component_mask], 90))
        margin_db = component_db - threshold_db
        confidence = float(np.clip(margin_db / (2.0 * threshold_margin_db), 0.0, 1.0))
        regions.append(EnergyRegion(
            t_start_s=t_low_s,
            t_end_s=t_high_s,
            f_low_hz=f_low_hz,
            f_high_hz=f_high_hz,
            confidence=confidence,
            energy_margin_db=margin_db,
        ))
    return regions