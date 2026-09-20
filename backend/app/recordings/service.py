from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel
from app.storage.service import StorageService

# bytes per complex sample for path-registerable formats
PATH_FORMAT_BYTES: dict[str, int] = {
    "complex64_le": 8,
    "float16_interleaved_le": 4,
    "int16_interleaved_le": 4,
}

SUPPORTED_PATH_FORMATS = ", ".join(sorted(PATH_FORMAT_BYTES))


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

    def register_local_path(
        self,
        *,
        path: str,
        name: str,
        data_format: str,
        sample_rate_hz: float,
        center_frequency_hz: float,
        label_space: str | None = None,
    ) -> RecordingModel:
        """Register an existing on-host IQ file WITHOUT copying it.

        Path mode is standalone-only: dataset members are never repurposed.
        """
        if sample_rate_hz <= 0:
            raise PlatformError("INVALID_RECORDING", "Sample rate must be positive.")
        if data_format not in PATH_FORMAT_BYTES:
            raise PlatformError(
                "INVALID_RECORDING",
                "Unsupported path data format. Use one of: complex64_le, float16_interleaved_le, int16_interleaved_le.",
            )
        candidate = Path(path)
        if not candidate.is_absolute():
            raise PlatformError("INVALID_RECORDING", "Path must be an absolute path.")
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise PlatformError(
                "RECORDING_PATH_NOT_FOUND", "Local IQ file was not found or is not a regular file.", 404
            )
        if not os.access(resolved, os.R_OK):
            raise PlatformError("RECORDING_PATH_NOT_READABLE", "Local IQ file is not readable.", 403)
        byte_size = resolved.stat().st_size
        bytes_per_sample = PATH_FORMAT_BYTES[data_format]
        if byte_size <= 0 or byte_size % bytes_per_sample:
            raise PlatformError(
                "INVALID_RECORDING",
                f"IQ byte length is not a non-empty multiple of {bytes_per_sample}.",
            )

        existing = self.session.scalar(
            select(RecordingModel).where(
                or_(
                    RecordingModel.external_path == str(resolved),
                    RecordingModel.data_path == str(resolved),
                )
            )
        )
        if existing is not None:
            if existing.dataset_id is not None:
                raise PlatformError(
                    "RECORDING_PATH_IS_DATASET_MEMBER",
                    "This path is a Dataset sample. Use the Dataset workspace instead.",
                    409,
                    {"recording_id": existing.id, "dataset_id": existing.dataset_id},
                )
            raise PlatformError(
                "ALREADY_REGISTERED",
                "This path is already registered as a standalone sample.",
                409,
                {"recording_id": existing.id},
            )

        num_samples = byte_size // bytes_per_sample
        duration_s = num_samples / sample_rate_hz
        half_band = sample_rate_hz / 2
        recording = RecordingModel(
            id=f"rec_{uuid4().hex}",
            name=name.strip() or resolved.stem,
            data_path=str(resolved),
            data_format=data_format,
            source="custom",
            external_path=str(resolved),
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=center_frequency_hz,
            frequency_low_hz=center_frequency_hz - half_band,
            frequency_high_hz=center_frequency_hz + half_band,
            num_samples=num_samples,
            duration_s=duration_s,
            dataset_name=None,
            dataset_split=None,
            label_space=label_space,
            has_ground_truth=False,
            dataset_id=None,
            sample_key=None,
        )
        self.session.add(recording)
        self.session.commit()
        self.session.refresh(recording)
        return recording

    def import_uploaded_iq(
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
        ground_truth: "Sequence | None" = None,
    ) -> RecordingModel:
        """Persist an uploaded IQ sample managed by WISA storage.

        ``complex64_le`` (the generic upload) and ``float16_interleaved_le`` (a
        SpaceNet ``.bin`` uploaded with its sidecar) are both accepted; the bytes
        are stored verbatim and read back through the same unified
        ``RecordingReader`` used by registered dataset members.
        """
        if sample_rate_hz <= 0:
            raise PlatformError("INVALID_RECORDING", "Sample rate must be positive.")
        bytes_per_sample = PATH_FORMAT_BYTES.get(data_format)
        if bytes_per_sample is None:
            raise PlatformError(
                "INVALID_RECORDING",
                "Unsupported data format. Use one of: complex64_le, float16_interleaved_le, int16_interleaved_le.",
            )

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
            if byte_size == 0 or byte_size % bytes_per_sample:
                raise PlatformError(
                    "INVALID_RECORDING",
                    f"{data_format} IQ must be non-empty and divisible by {bytes_per_sample} bytes.",
                )

            num_samples = byte_size // bytes_per_sample
            duration_s = num_samples / sample_rate_hz
            half_band = sample_rate_hz / 2
            recording_id = f"rec_{uuid4().hex}"
            final_dir = self.storage.recording_dir(recording_id)
            final_path = final_dir / "raw.iq"
            shutil.move(str(temp_path), str(final_path))

            relative_path = final_path.relative_to(self.data_root).as_posix()
            ground_truth_rows = self._ground_truth_rows(
                ground_truth, num_samples=num_samples, sample_rate_hz=sample_rate_hz
            )
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
                has_ground_truth=bool(ground_truth_rows),
            )
            self.session.add(recording)
            for row in ground_truth_rows:
                self.session.add(
                    GroundTruthModel(
                        id=f"gt_{uuid4().hex}",
                        recording_id=recording_id,
                        t_start_s=row.t_start_s,
                        t_end_s=row.t_end_s,
                        f_low_hz=row.f_low_hz,
                        f_high_hz=row.f_high_hz,
                        class_id=row.class_id,
                        class_name=row.class_name,
                    )
                )
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

    @staticmethod
    def _ground_truth_rows(ground_truth, *, num_samples: int, sample_rate_hz: float):
        """Ground truth already validated by the SpaceNet adapter against the real
        IQ duration (see ``app.recordings.spacenet_upload``)."""
        return list(ground_truth or [])
