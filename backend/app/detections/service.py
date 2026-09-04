from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel


class DetectionService:
    def __init__(self, session: Session):
        self.session = session

    def list_for_run(self, run_id: str) -> list[DetectionResultModel]:
        statement = select(DetectionResultModel).where(DetectionResultModel.run_id == run_id).order_by(DetectionResultModel.id)
        return list(self.session.scalars(statement).all())

    def get(self, detection_id: str) -> DetectionResultModel:
        detection = self.session.get(DetectionResultModel, detection_id)
        if detection is None:
            raise PlatformError("DETECTION_NOT_FOUND", "Detection result was not found.", 404)
        return detection
