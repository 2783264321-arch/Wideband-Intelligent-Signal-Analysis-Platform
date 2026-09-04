from typing import Any

from pydantic import BaseModel

from app.detections.model import DetectionResultModel


class DetectionRead(BaseModel):
    id: str
    run_id: str
    recording_id: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
    confidence: float
    scores_json: dict[str, Any] | None = None

    @classmethod
    def from_model(cls, model: DetectionResultModel) -> "DetectionRead":
        return cls(
            id=model.id,
            run_id=model.run_id,
            recording_id=model.run.recording_id,
            t_start_s=model.t_start_s,
            t_end_s=model.t_end_s,
            f_low_hz=model.f_low_hz,
            f_high_hz=model.f_high_hz,
            class_id=model.class_id,
            class_name=model.class_name,
            confidence=model.confidence,
            scores_json=model.scores_json,
        )


class FFTRead(BaseModel):
    frequency_hz: list[float]
    magnitude_db: list[float]
