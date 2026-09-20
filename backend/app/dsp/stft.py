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


def _cache_key(recording_id: str, nperseg: int, noverlap: int, nfft: int) -> str:
    payload = f"{recording_id}:stft:{nperseg}:{noverlap}:{nfft}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


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
) -> SpectrogramResult:
    """STFT preview that never loads the whole recording.

    A 20 s, 100 MHz recording has ~7.8M frames: computing (and caching) all of them
    needs tens of GB. The display cannot show them anyway, so the preview keeps at
    most ``target_frames`` columns, evenly spaced over the FULL duration, and reads
    only those windows (``target_frames * nperseg`` samples). The time axis therefore
    still spans the whole recording, which keeps the overlay mapping exact.
    """
    if num_samples <= 0:
        raise ValueError("Recording has no samples.")
    hop = max(nperseg - noverlap, 1)
    total_frames = max(1, 1 + (num_samples - nperseg) // hop) if num_samples >= nperseg else 1
    frame_stride = max(1, math.ceil(total_frames / max(target_frames, 1)))
    frame_positions = np.arange(0, total_frames, frame_stride, dtype=np.int64)[:target_frames]
    starts = frame_positions * hop
    starts = np.minimum(starts, max(num_samples - nperseg, 0))

    # One bounded gather: frames x nperseg samples (never the whole file).
    window_indices = (starts[:, None] + np.arange(nperseg, dtype=np.int64)[None, :]).ravel()
    window_indices = np.clip(window_indices, 0, max(num_samples - 1, 0))
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
) -> SpectrogramPreview:
    cache_dir = storage.spectrogram_cache_dir()
    key = _cache_key(recording.id, nperseg, noverlap, nfft)
    png_path = cache_dir / f"{key}.png"

    if not png_path.exists():
        path = resolve_recording_path(recording, data_root)
        result = compute_decimated_stft(
            path,
            recording.data_format,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            num_samples=int(recording.num_samples),
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_preview_png(png_path, result.magnitude_db)

    return SpectrogramPreview(
        representation="stft",
        image_url=f"/media/spectrograms/{png_path.name}",
        t_start_s=0.0,
        t_end_s=recording.duration_s,
        f_low_hz=recording.frequency_low_hz,
        f_high_hz=recording.frequency_high_hz,
    )
