from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.core.errors import PlatformError
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
