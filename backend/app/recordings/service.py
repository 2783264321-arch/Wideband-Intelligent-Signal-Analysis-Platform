from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.storage.service import StorageService


class RecordingService:
    def __init__(self, session: Session, storage: StorageService, data_root: Path):
        self.session = session
        self.storage = storage
        self.data_root = data_root.resolve()

    def list(self, limit: int = 100, offset: int = 0) -> tuple[list[RecordingModel], int]:
        if limit <= 0 or offset < 0:
            raise PlatformError("INVALID_LISTING", "limit must be positive and offset non-negative.")
        total = int(self.session.scalar(select(func.count()).select_from(RecordingModel)) or 0)
        items = list(
            self.session.scalars(
                select(RecordingModel).order_by(RecordingModel.created_at, RecordingModel.id).offset(offset).limit(limit)
            ).all()
        )
        return items, total

    def get(self, recording_id: str) -> RecordingModel:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", status_code=404)
        return recording

    def import_complex64(
        self,
        *,
        upload: UploadFile,
        name: str,
        sample_rate_hz: float,
        center_frequency_hz: float,
        data_format: str,
        dataset_name: str | None = None,
        dataset_split: str | None = None,
        label_space: str | None = None,
    ) -> RecordingModel:
        if sample_rate_hz <= 0:
            raise PlatformError("INVALID_RECORDING", "Sample rate must be positive.")
        if data_format != "complex64_le":
            raise PlatformError("INVALID_RECORDING", "Only complex64_le is supported by the V1 custom importer.")

        token = f"upload_{uuid4().hex}"
        temp_dir = self.storage.import_temp_dir(token)
        temp_path = temp_dir / "source.bin"
        final_dir: Path | None = None
        try:
            with temp_path.open("wb") as destination:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)

            byte_size = temp_path.stat().st_size
            if byte_size == 0 or byte_size % 8 != 0:
                raise PlatformError("INVALID_RECORDING", "complex64_le IQ must be non-empty and divisible by 8 bytes.")

            num_samples = byte_size // 8
            duration_s = num_samples / sample_rate_hz
            half_band = sample_rate_hz / 2
            recording_id = f"rec_{uuid4().hex}"
            final_dir = self.storage.recording_dir(recording_id)
            final_path = final_dir / "raw.iq"
            shutil.move(str(temp_path), str(final_path))

            relative_path = final_path.relative_to(self.data_root).as_posix()
            recording = RecordingModel(
                id=recording_id,
                name=name.strip() or recording_id,
                data_path=relative_path,
                data_format=data_format,
                sample_rate_hz=sample_rate_hz,
                center_frequency_hz=center_frequency_hz,
                frequency_low_hz=center_frequency_hz - half_band,
                frequency_high_hz=center_frequency_hz + half_band,
                num_samples=num_samples,
                duration_s=duration_s,
                dataset_name=dataset_name,
                dataset_split=dataset_split,
                label_space=label_space,
                has_ground_truth=False,
            )
            self.session.add(recording)
            self.session.commit()
            self.session.refresh(recording)
            return recording
        except Exception:
            self.session.rollback()
            if final_dir is not None and final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
