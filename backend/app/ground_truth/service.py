from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.core.signal_validation import validate_label, validate_physical_box
from app.ground_truth.model import GroundTruthModel
from app.ground_truth.schema import GroundTruthImport
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel


class GroundTruthService:
    def __init__(self, session: Session, label_service: LabelSpaceService):
        self.session = session
        self.label_service = label_service

    def list(self, recording_id: str) -> list[GroundTruthModel]:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        statement = select(GroundTruthModel).where(GroundTruthModel.recording_id == recording_id).order_by(GroundTruthModel.id)
        return list(self.session.scalars(statement).all())

    def replace(self, recording_id: str, payload: GroundTruthImport) -> list[GroundTruthModel]:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        if recording.label_space is not None and recording.label_space != payload.label_space:
            raise PlatformError("INVALID_GROUND_TRUTH", "Ground truth label space does not match the recording.")

        items: list[GroundTruthModel] = []
        seen_ids: set[str] = set()
        for item in payload.objects:
            if item.id in seen_ids:
                raise PlatformError("INVALID_GROUND_TRUTH", f"Duplicate ground-truth id: {item.id}")
            seen_ids.add(item.id)
            validate_physical_box(
                recording,
                t_start_s=item.t_start_s,
                t_end_s=item.t_end_s,
                f_low_hz=item.f_low_hz,
                f_high_hz=item.f_high_hz,
                error_code="INVALID_GROUND_TRUTH",
            )
            validate_label(
                self.label_service,
                label_space_id=payload.label_space,
                class_id=item.class_id,
                class_name=item.class_name,
                error_code="INVALID_GROUND_TRUTH",
            )
            items.append(
                GroundTruthModel(
                    id=item.id,
                    recording_id=recording_id,
                    t_start_s=item.t_start_s,
                    t_end_s=item.t_end_s,
                    f_low_hz=item.f_low_hz,
                    f_high_hz=item.f_high_hz,
                    class_id=item.class_id,
                    class_name=item.class_name,
                )
            )

        self.session.execute(delete(GroundTruthModel).where(GroundTruthModel.recording_id == recording_id))
        if items:
            self.session.add_all(items)
        recording.label_space = payload.label_space
        recording.has_ground_truth = bool(items)
        self.session.commit()
        return items
