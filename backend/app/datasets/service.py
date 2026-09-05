"""Register SpaceNet samples as external Recordings without copying binaries."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.datasets.spacenet import SpaceNetAdapter
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


@dataclass(frozen=True)
class RegistrationSummary:
    created: int
    skipped: int
    invalid: int

    @property
    def total(self) -> int:
        return self.created + self.skipped + self.invalid


class SpaceNetRegistrationService:
    def __init__(self, session: Session, label_space_root: Path):
        self.session = session
        self.label_space_root = Path(label_space_root)

    def register_directory(self, dataset_path: str, split: str = "test") -> RegistrationSummary:
        root = Path(dataset_path).resolve()
        if root.name in ("train", "test"):
            split = root.name
            root = root.parent
        split_root = root / split
        if not split_root.is_dir():
            raise PlatformError("SPACENET_SPLIT_NOT_FOUND", f"SpaceNet split '{split}' was not found.", 404)

        adapter = SpaceNetAdapter(root, self.label_space_root, "spacenet_14")
        stems = sorted({path.stem for path in split_root.glob("*.bin")} | {path.stem for path in split_root.glob("*.json")})

        created = 0
        skipped = 0
        invalid = 0
        for stem in stems:
            try:
                sample = adapter.load(split, stem)
            except PlatformError:
                invalid += 1
                continue
            external_path = str(sample.data_path.resolve())
            existing = self.session.scalar(
                select(RecordingModel).where(RecordingModel.external_path == external_path))
            if existing is not None:
                skipped += 1
                continue

            recording_id = f"rec_{uuid4().hex}"
            recording = RecordingModel(
                id=recording_id,
                name=sample.id,
                data_path=external_path,
                data_format=sample.data_format,
                source="spacenet",
                external_path=external_path,
                sample_rate_hz=sample.sample_rate_hz,
                center_frequency_hz=sample.center_frequency_hz,
                frequency_low_hz=sample.frequency_low_hz,
                frequency_high_hz=sample.frequency_high_hz,
                num_samples=sample.num_samples,
                duration_s=sample.duration_s,
                dataset_name="SpaceNet",
                dataset_split=split,
                label_space="spacenet_14",
                has_ground_truth=bool(sample.signals),
            )
            ground_truth = [
                GroundTruthModel(
                    id=f"gt_{uuid4().hex}",
                    recording_id=recording_id,
                    t_start_s=signal.t_start_s,
                    t_end_s=signal.t_end_s,
                    f_low_hz=signal.f_low_hz,
                    f_high_hz=signal.f_high_hz,
                    class_id=signal.class_id,
                    class_name=signal.class_name,
                )
                for signal in sample.signals
            ]
            self.session.add(recording)
            if ground_truth:
                self.session.add_all(ground_truth)
            created += 1

        self.session.commit()
        return RegistrationSummary(created=created, skipped=skipped, invalid=invalid)