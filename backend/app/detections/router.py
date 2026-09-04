import math

from fastapi import APIRouter, Query, Request
import numpy as np

from app.core.errors import PlatformError
from app.detections.schema import DetectionRead, FFTRead
from app.detections.service import DetectionService
from app.dsp.iq import read_iq

router = APIRouter(tags=["detections"])


@router.get("/api/analysis-runs/{run_id}/detections", response_model=list[DetectionRead])
def list_run_detections(run_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return [DetectionRead.from_model(item) for item in DetectionService(session).list_for_run(run_id)]


@router.get("/api/detections/{detection_id}", response_model=DetectionRead)
def get_detection(detection_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return DetectionRead.from_model(DetectionService(session).get(detection_id))


@router.get("/api/detections/{detection_id}/fft", response_model=FFTRead)
def get_detection_fft(detection_id: str, request: Request, max_points: int = Query(2048, ge=16, le=8192)):
    with request.app.state.database.session_factory() as session:
        detection = DetectionService(session).get(detection_id)
        recording = detection.run.recording
        start_sample = max(0, int(math.floor(detection.t_start_s * recording.sample_rate_hz)))
        end_sample = min(recording.num_samples, int(math.ceil(detection.t_end_s * recording.sample_rate_hz)))
        iq = read_iq(recording, request.app.state.settings.data_root, start_sample, max(end_sample - start_sample, 1))

    if iq.size < 2:
        raise PlatformError("INVALID_RECORDING", "Detection segment is too short for FFT display.")
    window = np.hanning(iq.size)
    spectrum = np.fft.fftshift(np.fft.fft(iq * window))
    frequencies = np.fft.fftshift(np.fft.fftfreq(iq.size, d=1.0 / recording.sample_rate_hz)) + recording.center_frequency_hz
    magnitude_db = 20.0 * np.log10(np.maximum(np.abs(spectrum), np.finfo(np.float32).eps))
    stride = max(1, math.ceil(iq.size / max_points))
    return FFTRead(
        frequency_hz=np.asarray(frequencies[::stride], dtype=float).tolist(),
        magnitude_db=np.asarray(magnitude_db[::stride], dtype=float).tolist(),
    )
