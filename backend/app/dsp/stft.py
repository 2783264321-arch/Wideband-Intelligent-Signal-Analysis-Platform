from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import stft as scipy_stft

from app.recordings.model import RecordingModel
from app.recordings.reader import read_samples_at_from_path, resolve_recording_path
from app.storage.service import StorageService

# The display cannot resolve more columns than this; keeping the preview bounded
# keeps both the render and the cache independent of recording length.
MAX_PREVIEW_FRAMES = 2048


@dataclass(frozen=True)
class SpectrogramResult:
    magnitude_db: np.ndarray
    time_axis_s: np.ndarray
    frequency_axis_hz: np.ndarray


@dataclass(frozen=True)
class SpectrogramPreview:
    representation: str
    image_url: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    num_frames: int = 0


def compute_stft(
    iq: np.ndarray,
    sample_rate_hz: float,
    center_frequency_hz: float,
    nperseg: int = 512,
    noverlap: int = 256,
    nfft: int = 512,
) -> SpectrogramResult:
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
    magnitude_db = 20.0 * np.log10(np.maximum(np.abs(zxx), np.finfo(np.float32).eps))
    frequency_axis_hz = frequencies + center_frequency_hz

    return SpectrogramResult(
        magnitude_db=np.asarray(magnitude_db, dtype=np.float32),
        time_axis_s=np.asarray(times, dtype=np.float64),
        frequency_axis_hz=np.asarray(frequency_axis_hz, dtype=np.float64),
    )


def _window_cache_token(start_sample: int, end_sample: int) -> str:
    # Round to a 1 ms grid so tiny float noise does not spawn a new cache file per
    # request, while distinct windows stay distinct.
    return f"{start_sample // 1000}:{end_sample // 1000}"


def _cache_key(
    recording_id: str,
    nperseg: int,
    noverlap: int,
    nfft: int,
    window: str | None = None,
) -> str:
    payload = f"{recording_id}:stft:{nperseg}:{noverlap}:{nfft}"
    if window is not None:
        payload = f"{payload}:{window}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def compute_decimated_stft(
    path: Path,
    data_format: str,
    *,
    sample_rate_hz: float,
    center_frequency_hz: float,
    num_samples: int,
    nperseg: int = 512,
    noverlap: int = 256,
    nfft: int = 512,
    target_frames: int = MAX_PREVIEW_FRAMES,
    start_sample: int = 0,
) -> SpectrogramResult:
    """STFT preview that never loads the whole recording.

    A 20 s, 100 MHz recording has ~7.8M frames: computing (and caching) all of them
    needs tens of GB. The display cannot show them anyway, so the preview keeps at
    most ``target_frames`` columns, evenly spaced over the requested span, and reads
    only those windows (``target_frames * nperseg`` samples).

    ``start_sample`` shifts the span into the recording: with ``start_sample=0`` and
    ``num_samples`` = the whole file the preview spans the full duration (the overview
    case). Passing a sub-range (``start_sample`` + in-window ``num_samples``) raises the
    column resolution for that window by the same factor, which is how a long recording
    stays inspectable. The returned time axis is absolute (recording clock), so overlay
    mapping stays exact either way.
    """
    if num_samples <= 0:
        raise ValueError("Recording has no samples.")
    if start_sample < 0:
        raise ValueError("start_sample must be non-negative.")
    hop = max(nperseg - noverlap, 1)
    total_frames = max(1, 1 + (num_samples - nperseg) // hop) if num_samples >= nperseg else 1
    frame_stride = max(1, math.ceil(total_frames / max(target_frames, 1)))
    frame_positions = np.arange(0, total_frames, frame_stride, dtype=np.int64)[:target_frames]
    starts = frame_positions * hop
    starts = np.minimum(starts, max(num_samples - nperseg, 0))
    starts = starts + start_sample

    # One bounded gather: frames x nperseg samples (never the whole file).
    window_indices = (starts[:, None] + np.arange(nperseg, dtype=np.int64)[None, :]).ravel()
    window_indices = np.clip(window_indices, 0, max(start_sample + num_samples - 1, 0))
    block = read_samples_at_from_path(path, data_format, window_indices)
    frames = block.reshape(starts.size, nperseg)

    window = np.hanning(nperseg).astype(np.float32)
    spectra = np.fft.fftshift(np.fft.fft(frames * window, n=nfft, axis=1), axes=1)
    magnitude_db = 20.0 * np.log10(np.maximum(np.abs(spectra), np.finfo(np.float32).eps))

    time_axis_s = (starts + nperseg / 2.0) / sample_rate_hz
    baseband_hz = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / sample_rate_hz))
    return SpectrogramResult(
        magnitude_db=np.asarray(magnitude_db.T, dtype=np.float32),
        time_axis_s=np.asarray(time_axis_s, dtype=np.float64),
        frequency_axis_hz=np.asarray(baseband_hz + center_frequency_hz, dtype=np.float64),
    )


def _write_preview_png(path: Path, magnitude_db: np.ndarray) -> None:
    plt.imsave(path, magnitude_db, origin="lower", cmap="viridis", format="png")


def get_or_create_stft_preview(
    recording: RecordingModel,
    *,
    data_root: Path,
    storage: StorageService,
    nperseg: int = 512,
    noverlap: int = 256,
    nfft: int = 512,
    t_start_s: float | None = None,
    t_end_s: float | None = None,
) -> SpectrogramPreview:
    """Full-duration overview when no window is given; a high-resolution slice otherwise.

    A window (``t_start_s``/``t_end_s``) keeps the same column budget but spends it on a
    shorter span, so the per-column time resolution improves by ``duration / window``.
    Results are cached per window.
    """
    duration_s = float(recording.duration_s)
    if duration_s <= 0:
        raise ValueError("Recording has no duration.")
    start_s = 0.0 if t_start_s is None else max(0.0, float(t_start_s))
    end_s = duration_s if t_end_s is None else float(t_end_s)
    if not (0.0 <= start_s < end_s <= duration_s):
        raise ValueError("Requested window must satisfy 0 <= t_start_s < t_end_s <= duration.")
    if end_s - start_s < nperseg / recording.sample_rate_hz:
        raise ValueError("Requested window is shorter than a single STFT frame.")

    is_overview = start_s <= 0.0 and end_s >= duration_s
    start_sample = 0 if is_overview else int(round(start_s * recording.sample_rate_hz))
    window_samples = (
        int(recording.num_samples)
        if is_overview
        else max(nperseg, int(round((end_s - start_s) * recording.sample_rate_hz)))
    )
    window_samples = min(window_samples, int(recording.num_samples) - start_sample)

    window_token = None if is_overview else _window_cache_token(start_sample, start_sample + window_samples)
    cache_dir = storage.spectrogram_cache_dir()
    key = _cache_key(recording.id, nperseg, noverlap, nfft, window_token)
    png_path = cache_dir / f"{key}.png"

    if not png_path.exists():
        path = resolve_recording_path(recording, data_root)
        result = compute_decimated_stft(
            path,
            recording.data_format,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            num_samples=window_samples,
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
            start_sample=start_sample,
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_preview_png(png_path, result.magnitude_db)
        num_frames = int(result.time_axis_s.size)
    else:
        num_frames = 0

    actual_start_s = (start_sample / recording.sample_rate_hz) if not is_overview else 0.0
    actual_end_s = (
        (start_sample + window_samples) / recording.sample_rate_hz if not is_overview else duration_s
    )
    return SpectrogramPreview(
        representation="stft",
        image_url=f"/media/spectrograms/{png_path.name}",
        t_start_s=min(actual_start_s, duration_s),
        t_end_s=min(actual_end_s, duration_s),
        f_low_hz=recording.frequency_low_hz,
        f_high_hz=recording.frequency_high_hz,
        num_frames=num_frames,
    )
