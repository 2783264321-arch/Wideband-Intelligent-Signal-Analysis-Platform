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


# Time hop as a fraction of nperseg (noverlap = nperseg - hop).
_HOP_FRACTION = 0.5
# Analysis window used when adapting to wideband captures; the coarse time grid comes
# from the hop (noverlap), because scipy requires nfft >= nperseg.
_ADAPTED_NPERSEG = 4096
# Morphological closing window, in seconds, used when time_resolution_s is set.
_CLOSING_SECONDS = 0.05
# Cap the samples fed to the per-region 90th-percentile so a very large region
# cannot allocate a huge temporary (the percentile is stable on a subsample).
_PERCENTILE_SAMPLE_LIMIT = 200_000


def _resolve_frame_parameters(
    *,
    iq_size: int,
    sample_rate_hz: float,
    nperseg: int,
    noverlap: int,
    closing_size: int | tuple[int, int],
    time_resolution_s: float | None,
) -> tuple[int, int, tuple[int, int]]:
    """Pick STFT/morphology sizes, adapting to the sample rate when asked.

    With ``time_resolution_s=None`` the caller's values pass through unchanged
    (legacy/short-sample behaviour). Otherwise the window stays bounded
    (``_ADAPTED_NPERSEG``) and the coarse time grid comes from the hop, which is what
    ``noverlap`` controls -- scipy requires ``nfft >= nperseg``, so enlarging the
    window itself is not an option.
    """
    if isinstance(closing_size, tuple):
        resolved_closing = (int(closing_size[0]), int(closing_size[1]))
    else:
        resolved_closing = (int(closing_size), int(closing_size))

    if time_resolution_s is None or time_resolution_s <= 0:
        return nperseg, noverlap, resolved_closing

    hop_samples = max(int(round(time_resolution_s * sample_rate_hz)), 1)
    adapted_nperseg = min(_ADAPTED_NPERSEG, iq_size)
    # noverlap = nperseg - hop; a negative value means non-overlapping frames, which
    # scipy does not accept, so clamp the hop to at most one full window.
    effective_hop = min(hop_samples, adapted_nperseg)
    adapted_noverlap = max(adapted_nperseg - effective_hop, 0)

    if resolved_closing == (5, 5):
        # Default closing: size it from the block clock instead of a fixed 5 bins.
        closing_time_bins = max(int(round(_CLOSING_SECONDS / time_resolution_s)), 1)
        resolved_closing = (closing_time_bins, closing_time_bins)
    return adapted_nperseg, adapted_noverlap, resolved_closing


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
    closing_size: int | tuple[int, int] = 5,
    min_area: int = 100,
    min_duration_s: float = 0.0,
    min_bandwidth_hz: float = 0.0,
    time_offset_s: float = 0.0,
    time_resolution_s: float | None = None,
) -> list[EnergyRegion]:
    """Detect energy regions in one contiguous IQ block.

    ``time_offset_s`` is the block's start time on the recording clock; it is added
    to every region so a chunked caller (which feeds the recording in blocks) still
    reports absolute times.

    ``time_resolution_s`` adapts the STFT to wideband captures. Default parameters
    (``nperseg=512``) give a 2.56 us time hop at 100 MHz, which shatters a single
    signal into thousands of slivers. When set, ``nperseg`` is derived so the time
    hop is about ``time_resolution_s`` and the morphological closing window is sized
    in seconds, which merges those slivers back into one region. Explicit
    ``nperseg``/``noverlap`` and ``closing_size`` still win when changed from their
    defaults, so short-sample behaviour is untouched.
    """
    if iq.ndim != 1 or iq.size == 0:
        raise ValueError("IQ input must be a non-empty 1D array.")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive.")

    resolved_nperseg, resolved_noverlap, resolved_closing = _resolve_frame_parameters(
        iq_size=iq.size,
        sample_rate_hz=sample_rate_hz,
        nperseg=nperseg,
        noverlap=noverlap,
        closing_size=closing_size,
        time_resolution_s=time_resolution_s,
    )

    effective_nperseg = min(resolved_nperseg, iq.size)
    effective_noverlap = min(resolved_noverlap, max(effective_nperseg - 1, 0))
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
        t_low_s = time_axis_s[time_indices.min()] - time_hop_s / 2.0 + time_offset_s
        t_high_s = time_axis_s[time_indices.max()] + time_hop_s / 2.0 + time_offset_s
        f_low_hz = frequency_axis_hz[frequency_indices.min()] - time_bin_hz / 2.0
        f_high_hz = frequency_axis_hz[frequency_indices.max()] + time_bin_hz / 2.0
        if t_high_s - t_low_s < min_duration_s:
            continue
        if f_high_hz - f_low_hz < min_bandwidth_hz:
            continue
        # Gather only the region's cells instead of materialising the whole
        # sub-block, and subsample very large regions (the percentile is stable).
        cell_values = power_db[frequency_indices, time_indices]
        if cell_values.size > _PERCENTILE_SAMPLE_LIMIT:
            stride = int(np.ceil(cell_values.size / _PERCENTILE_SAMPLE_LIMIT))
            cell_values = cell_values[::stride]
        component_db = float(np.percentile(cell_values, 90))
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