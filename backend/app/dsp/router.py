from typing import Literal
import math

import numpy as np

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.core.errors import PlatformError
from app.dsp.iq import read_iq
from app.dsp.stft import get_or_create_stft_preview
from app.recordings.service import RecordingService

router = APIRouter(prefix="/api/recordings", tags=["dsp"])


class SpectrogramRead(BaseModel):
    representation: str
    image_url: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float


@router.get("/{recording_id}/spectrogram", response_model=SpectrogramRead)
def get_spectrogram(
    recording_id: str,
    request: Request,
    representation: Literal["stft"] = Query("stft"),
):
    if representation != "stft":
        raise PlatformError("INVALID_REPRESENTATION", "Only STFT is implemented in the core slice.")

    with request.app.state.database.session_factory() as session:
        recording = RecordingService(
            session,
            request.app.state.storage,
            request.app.state.settings.data_root,
        ).get(recording_id)
        return get_or_create_stft_preview(
            recording,
            data_root=request.app.state.settings.data_root,
            storage=request.app.state.storage,
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
        iq = read_iq(recording, request.app.state.settings.data_root, start_sample, max(end_sample - start_sample, 1))

    stride = max(1, math.ceil(iq.size / max_points))
    indices = np.arange(iq.size, dtype=np.int64)[::stride]
    sampled = iq[::stride]
    times = (start_sample + indices) / recording.sample_rate_hz
    return WaveformRead(
        time_s=np.asarray(times, dtype=float).tolist(),
        i=np.asarray(sampled.real, dtype=float).tolist(),
        q=np.asarray(sampled.imag, dtype=float).tolist(),
    )
