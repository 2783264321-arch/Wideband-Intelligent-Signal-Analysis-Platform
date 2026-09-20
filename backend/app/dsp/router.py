from typing import Literal
import math

import numpy as np

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.core.errors import PlatformError
from app.dsp.spectrum import compute_spectrum_preview
from app.dsp.stft import get_or_create_stft_preview
from app.recordings.reader import read_samples_at_from_path, resolve_recording_path
from app.recordings.service import RecordingService

router = APIRouter(prefix="/api/recordings", tags=["dsp"])


class SpectrogramRead(BaseModel):
    representation: str
    image_url: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    num_frames: int = 0


@router.get("/{recording_id}/spectrogram", response_model=SpectrogramRead)
def get_spectrogram(
    recording_id: str,
    request: Request,
    representation: Literal["stft"] = Query("stft"),
    t_start_s: float | None = Query(None, ge=0),
    t_end_s: float | None = Query(None, gt=0),
):
    if representation != "stft":
        raise PlatformError("INVALID_REPRESENTATION", "Only STFT is implemented in the core slice.")
    if (t_start_s is None) != (t_end_s is None):
        raise PlatformError("INVALID_RECORDING", "t_start_s and t_end_s must be given together.")

    with request.app.state.database.session_factory() as session:
        recording = RecordingService(
            session,
            request.app.state.storage,
            request.app.state.settings.data_root,
        ).get(recording_id)
        try:
            return get_or_create_stft_preview(
                recording,
                data_root=request.app.state.settings.data_root,
                storage=request.app.state.storage,
                t_start_s=t_start_s,
                t_end_s=t_end_s,
            )
        except ValueError as error:
            raise PlatformError("INVALID_RECORDING", str(error)) from error


class SpectrumRead(BaseModel):
    frequency_hz: list[float]
    power_db: list[float]
    fft_size: int
    segment_count: int


@router.get("/{recording_id}/spectrum", response_model=SpectrumRead)
def get_spectrum(
    recording_id: str,
    request: Request,
    fft_size: int = Query(4096, ge=256, le=16384),
    segments: int = Query(8, ge=1, le=32),
):
    with request.app.state.database.session_factory() as session:
        recording = RecordingService(
            session,
            request.app.state.storage,
            request.app.state.settings.data_root,
        ).get(recording_id)
        preview = compute_spectrum_preview(
            recording,
            data_root=request.app.state.settings.data_root,
            fft_size=fft_size,
            segments=segments,
        )
    return SpectrumRead(
        frequency_hz=preview.frequency_hz,
        power_db=preview.power_db,
        fft_size=preview.fft_size,
        segment_count=preview.segment_count,
    )


class WaveformRead(BaseModel):
    time_s: list[float]
    i: list[float]
    q: list[float]


@router.get("/{recording_id}/waveform", response_model=WaveformRead)
def get_waveform(
    recording_id: str,
    request: Request,
    t_start_s: float = Query(..., ge=0),
    t_end_s: float = Query(..., gt=0),
    max_points: int = Query(4000, ge=16, le=20000),
):
    with request.app.state.database.session_factory() as session:
        recording = RecordingService(
            session,
            request.app.state.storage,
            request.app.state.settings.data_root,
        ).get(recording_id)
        if not (0 <= t_start_s < t_end_s <= recording.duration_s):
            raise PlatformError("INVALID_RECORDING", "Waveform time range must lie inside the recording.")
        start_sample = max(0, int(math.floor(t_start_s * recording.sample_rate_hz)))
        end_sample = min(recording.num_samples, int(math.ceil(t_end_s * recording.sample_rate_hz)))
        span = max(end_sample - start_sample, 1)
        # Decimate FIRST and read only the samples that will be drawn. Reading the
        # whole requested span (a 20 s, 100 MHz recording is 16 GB as complex64)
        # would exhaust memory for a waveform preview.
        if span <= max_points:
            relative = np.arange(span, dtype=np.int64)
        else:
            stride = max(1, math.ceil(span / max_points))
            relative = np.arange(0, span, stride, dtype=np.int64)[:max_points]
        path = resolve_recording_path(recording, request.app.state.settings.data_root)
        sampled = read_samples_at_from_path(
            path, recording.data_format, start_sample + relative
        )

    indices = start_sample + relative
    times = indices / recording.sample_rate_hz
    return WaveformRead(
        time_s=np.asarray(times, dtype=float).tolist(),
        i=np.asarray(sampled.real, dtype=float).tolist(),
        q=np.asarray(sampled.imag, dtype=float).tolist(),
    )
