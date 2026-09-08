"""Platform-native frozen AHLP candidate purification (Task 12C).

Reproduces the historical frozen ZoomSpec AHLP stage (``ahlp.py ::
purify_candidate``) as an independent production implementation:

    whole-recording complex64 IQ + CPNProposal
    -> context crop (floor start / ceil end, exclusive end)
    -> absolute-sample-index complex frequency translation
    -> score-dependent Hamming low-pass + exact FFT "same" convolution
    -> decimation (index-0 phase)
    -> PurifiedCandidate (complex64)

Independence contract:
- Pure NumPy; no Torch / Ultralytics / SciPy / FastAPI / SQLAlchemy.
- No filesystem I/O, no recording reader, no DB access.
- No legacy ZoomSpec imports and no historical deployment paths.
- Frozen AHLP parameters are internal constants and are NOT public options.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

# Frozen pipeline parameters. A future experiment with different values is a
# different pipeline/version; they are intentionally not caller options.
_CONTEXT_RATIO = 0.1
_KAPPA = 0.2
_ETA = 0.1
_ORDER_CONSTANT = 3.3
_MIN_NUMTAPS = 31
_MAX_NUMTAPS = 4095
_CUTOFF_SCALE = 1.0


@dataclass(frozen=True)
class AHLPDiagnostics:
    beta: float
    requested_lowpass_hz: float
    frequency_shift_hz: float
    numtaps: int
    numtaps_capped: bool
    input_samples: int
    output_samples: int
    crop_start_sample: int
    crop_end_sample: int
    identity_filter: bool


@dataclass(frozen=True)
class PurifiedCandidate:
    proposal: CPNProposal
    iq: np.ndarray
    sample_rate_hz: float
    crop_t_start_s: float
    crop_t_end_s: float
    lowpass_hz: float
    decimation: int
    diagnostics: AHLPDiagnostics

    def validate(self) -> None:
        if self.iq.ndim != 1 or not np.iscomplexobj(self.iq) or self.iq.size == 0:
            raise ValueError("purified iq must be a non-empty complex vector")
        if self.iq.dtype != np.complex64:
            raise ValueError("purified iq must be complex64")
        if self.sample_rate_hz <= 0 or self.lowpass_hz <= 0 or self.decimation < 1:
            raise ValueError("invalid AHLP metadata")
        if self.sample_rate_hz + 1e-6 < 2.0 * self.lowpass_hz:
            raise ValueError("post-decimation Nyquist condition is violated")
        if not self.crop_t_start_s < self.crop_t_end_s:
            raise ValueError("crop interval must be non-empty")


def _frequency_shift_hz(proposal_center_hz: float, center_frequency_hz: float) -> float:
    return float(proposal_center_hz - center_frequency_hz)


def _oscillator(n0: int, n1: int, frequency_shift_hz: float, sample_rate_hz: float) -> np.ndarray:
    global_index = np.arange(n0, n1, dtype=np.float64)
    return np.exp(-1j * 2.0 * np.pi * frequency_shift_hz * global_index / sample_rate_hz)


def _beta(score: float) -> float:
    return 1.0 + _KAPPA * (1.0 - score)


def _design_hamming_lowpass(sample_rate_hz: float, cutoff_hz: float) -> np.ndarray:
    if not 0 < cutoff_hz < 0.5 * sample_rate_hz:
        raise ValueError("cutoff must lie strictly between DC and Nyquist")
    transition_hz = _ETA * cutoff_hz
    numtaps = int(math.ceil(_ORDER_CONSTANT * sample_rate_hz / transition_hz))
    numtaps = max(_MIN_NUMTAPS, min(_MAX_NUMTAPS, numtaps))
    if numtaps % 2 == 0:
        numtaps += 1 if numtaps < _MAX_NUMTAPS else -1
    n = np.arange(numtaps, dtype=np.float64) - 0.5 * (numtaps - 1)
    cutoff_ratio = cutoff_hz / sample_rate_hz
    taps = 2.0 * cutoff_ratio * np.sinc(2.0 * cutoff_ratio * n)
    taps *= np.hamming(numtaps)
    taps /= taps.sum()
    return taps.astype(np.float64)


def _fft_convolve_same(signal: np.ndarray, taps: np.ndarray) -> np.ndarray:
    full_size = signal.size + taps.size - 1
    fft_length = 1 << (full_size - 1).bit_length()
    result = np.fft.ifft(np.fft.fft(signal, fft_length) * np.fft.fft(taps, fft_length))
    start = (taps.size - 1) // 2
    return result[start : start + signal.size].astype(np.complex64)


def _decimation(sample_rate_hz: float, lowpass_hz: float) -> int:
    return max(1, int(math.floor(sample_rate_hz / (2.0 * lowpass_hz))))


def purify_candidate(
    iq: np.ndarray,
    *,
    sample_rate_hz: float,
    center_frequency_hz: float,
    proposal: CPNProposal,
) -> PurifiedCandidate:
    """Purify one physical CPN proposal into a fixed-length-free candidate.

    The whole recording IQ is required because historical frequency
    translation uses absolute recording sample indices; the oscillator phase
    origin is the global sample index, not a crop-local zero.
    """
    if iq.ndim != 1:
        raise ValueError(f"iq must be one-dimensional, got {iq.ndim}D")
    if iq.size == 0:
        raise ValueError("iq must be non-empty")
    if iq.dtype != np.complex64:
        raise ValueError(f"iq must be complex64, got {iq.dtype}")
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive finite value")
    if not np.isfinite(center_frequency_hz):
        raise ValueError("center_frequency_hz must be finite")
    if not (
        np.isfinite(proposal.t_start_s)
        and np.isfinite(proposal.t_end_s)
        and np.isfinite(proposal.f_low_hz)
        and np.isfinite(proposal.f_high_hz)
        and np.isfinite(proposal.confidence)
    ):
        raise ValueError("proposal contains NaN or Inf")
    if not (proposal.t_start_s < proposal.t_end_s and proposal.f_low_hz < proposal.f_high_hz):
        raise ValueError("proposal intervals must be non-empty")
    if not 0.0 <= proposal.confidence <= 1.0:
        raise ValueError("proposal confidence must be in [0, 1]")

    duration = proposal.t_end_s - proposal.t_start_s
    crop_requested_start = proposal.t_start_s - _CONTEXT_RATIO * duration
    crop_requested_end = proposal.t_end_s + _CONTEXT_RATIO * duration
    recording_duration = iq.size / sample_rate_hz
    crop_start = max(0.0, crop_requested_start)
    crop_end = min(recording_duration, crop_requested_end)
    n0 = max(0, int(math.floor(crop_start * sample_rate_hz)))
    n1 = min(iq.size, int(math.ceil(crop_end * sample_rate_hz)))
    if n1 <= n0:
        raise ValueError("proposal produces an empty I/Q crop")

    proposal_center_hz = 0.5 * (proposal.f_low_hz + proposal.f_high_hz)
    shift_hz = _frequency_shift_hz(proposal_center_hz, center_frequency_hz)
    oscillator = _oscillator(n0, n1, shift_hz, sample_rate_hz)
    baseband = iq[n0:n1] * oscillator

    beta = _beta(proposal.confidence)
    requested_lowpass_hz = 0.5 * beta * (proposal.f_high_hz - proposal.f_low_hz) * _CUTOFF_SCALE
    nyquist_below = np.nextafter(0.5 * sample_rate_hz, 0.0)
    lowpass_hz = min(requested_lowpass_hz, nyquist_below)
    if lowpass_hz <= 0:
        raise ValueError("proposal bandwidth yields a non-positive low-pass cutoff")

    identity_filter = requested_lowpass_hz >= 0.5 * sample_rate_hz
    if identity_filter:
        filtered = baseband.astype(np.complex64)
        taps = np.asarray([1.0], dtype=np.float64)
    else:
        taps = _design_hamming_lowpass(sample_rate_hz, lowpass_hz)
        filtered = _fft_convolve_same(baseband, taps)

    decimation = _decimation(sample_rate_hz, lowpass_hz)
    purified = filtered[::decimation]

    diagnostics = AHLPDiagnostics(
        beta=beta,
        requested_lowpass_hz=float(requested_lowpass_hz),
        frequency_shift_hz=shift_hz,
        numtaps=int(taps.size),
        numtaps_capped=bool(taps.size >= _MAX_NUMTAPS),
        input_samples=int(n1 - n0),
        output_samples=int(purified.size),
        crop_start_sample=n0,
        crop_end_sample=n1,
        identity_filter=identity_filter,
    )
    candidate = PurifiedCandidate(
        proposal=proposal,
        iq=purified,
        sample_rate_hz=sample_rate_hz / decimation,
        crop_t_start_s=n0 / sample_rate_hz,
        crop_t_end_s=n1 / sample_rate_hz,
        lowpass_hz=float(lowpass_hz),
        decimation=decimation,
        diagnostics=diagnostics,
    )
    candidate.validate()
    return candidate