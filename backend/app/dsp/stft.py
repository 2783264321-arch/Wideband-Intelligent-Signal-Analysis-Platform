from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import stft as scipy_stft

from app.recordings.model import RecordingModel
from app.storage.service import StorageService
from app.dsp.iq import read_iq


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
    npz_path = cache_dir / f"{key}.npz"
    png_path = cache_dir / f"{key}.png"

    if not npz_path.exists() or not png_path.exists():
        iq = read_iq(recording, data_root)
        result = compute_stft(
            iq,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
        )
        np.savez_compressed(
            npz_path,
            magnitude_db=result.magnitude_db,
            time_axis_s=result.time_axis_s,
            frequency_axis_hz=result.frequency_axis_hz,
        )
        _write_preview_png(png_path, result.magnitude_db)

    return SpectrogramPreview(
        representation="stft",
        image_url=f"/media/spectrograms/{png_path.name}",
        t_start_s=0.0,
        t_end_s=recording.duration_s,
        f_low_hz=recording.frequency_low_hz,
        f_high_hz=recording.frequency_high_hz,
    )
