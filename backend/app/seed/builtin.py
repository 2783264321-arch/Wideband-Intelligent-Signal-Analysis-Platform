"""Built-in delivery data: a tiny dataset + one standalone sample.

A delivery copy of the platform must be usable on a machine that does NOT have the
~30 GB SpaceNet corpus. On startup the platform therefore seeds, from the bundled
``seed_data/`` directory:

* ``Mini-SpaceNet`` — a three-sample dataset registered through the real SpaceNet
  adapter (same manifest, fingerprints, and ground truth as a full registration);
* a standalone sample ``3`` — the same logical capture registered as an
  independent sample, with ground truth.

Seeding is idempotent and best-effort: a missing or invalid seed directory never
blocks startup, and existing rows are never duplicated or overwritten.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasets.model import DatasetModel
from app.datasets.service import SpaceNetRegistrationService
from app.datasets.spacenet import SpaceNetAdapter
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

logger = logging.getLogger(__name__)

SEED_DIRNAME = "seed_data"
SEED_DATASET_DIRNAME = "mini-spacenet"
SEED_STANDALONE_DIRNAME = "standalone"
SEED_DATASET_NAME = "Mini-SpaceNet"
SEED_SPLIT = "test"
SEED_ADAPTER_ID = "spacenet"
STANDALONE_SAMPLE_ID = "3"
STANDALONE_LABEL_SPACE = "spacenet_14"
STANDALONE_SOURCE = "builtin"
STANDALONE_RECORDING_ID = f"rec_seed_{STANDALONE_SAMPLE_ID}"


@dataclass(frozen=True)
class SeedOutcome:
    dataset_status: str  # "created" | "already_present" | "unavailable"
    dataset_id: str | None
    standalone_status: str  # "created" | "already_present" | "unavailable"
    standalone_recording_id: str | None

    @property
    def changed(self) -> bool:
        return "created" in (self.dataset_status, self.standalone_status)


def seed_root_for(project_root: Path) -> Path:
    return Path(project_root) / SEED_DIRNAME


def seed_builtin_data(
    session: Session, *, project_root: Path, label_space_root: Path
) -> SeedOutcome:
    """Idempotently register the bundled Mini-SpaceNet dataset and sample 3."""
    root = seed_root_for(project_root)
    dataset_status, dataset_id = _seed_dataset(session, root, label_space_root)
    standalone_status, standalone_recording_id = _seed_standalone(session, root, label_space_root)
    return SeedOutcome(
        dataset_status=dataset_status,
        dataset_id=dataset_id,
        standalone_status=standalone_status,
        standalone_recording_id=standalone_recording_id,
    )


def _seed_dataset(session: Session, root: Path, label_space_root: Path) -> tuple[str, str | None]:
    dataset_dir = root / SEED_DATASET_DIRNAME
    if not (dataset_dir / SEED_SPLIT).is_dir():
        return "unavailable", None
    existing = session.scalar(
        select(DatasetModel).where(
            DatasetModel.name == SEED_DATASET_NAME,
            DatasetModel.split == SEED_SPLIT,
            DatasetModel.adapter_id == SEED_ADAPTER_ID,
        )
    )
    if existing is not None:
        return "already_present", existing.id
    summary = SpaceNetRegistrationService(session, Path(label_space_root)).register_directory(
        str(dataset_dir), SEED_SPLIT, name=SEED_DATASET_NAME
    )
    return "created", summary.dataset_id


def _seed_standalone(session: Session, root: Path, label_space_root: Path) -> tuple[str, str | None]:
    standalone_dir = root / SEED_STANDALONE_DIRNAME
    data_path = standalone_dir / SEED_SPLIT / f"{STANDALONE_SAMPLE_ID}.bin"
    if not data_path.is_file():
        return "unavailable", None
    resolved = str(data_path.resolve())
    existing = session.scalar(
        select(RecordingModel).where(RecordingModel.external_path == resolved)
    )
    if existing is not None:
        return "already_present", existing.id

    adapter = SpaceNetAdapter(standalone_dir, Path(label_space_root), STANDALONE_LABEL_SPACE)
    sample = adapter.load(SEED_SPLIT, STANDALONE_SAMPLE_ID)
    recording = RecordingModel(
        id=STANDALONE_RECORDING_ID,
        name=STANDALONE_SAMPLE_ID,
        data_path=resolved,
        data_format=sample.data_format,
        source=STANDALONE_SOURCE,
        external_path=resolved,
        sample_rate_hz=sample.sample_rate_hz,
        center_frequency_hz=sample.center_frequency_hz,
        frequency_low_hz=sample.frequency_low_hz,
        frequency_high_hz=sample.frequency_high_hz,
        num_samples=sample.num_samples,
        duration_s=sample.duration_s,
        dataset_name=None,
        dataset_split=None,
        label_space=STANDALONE_LABEL_SPACE,
        has_ground_truth=bool(sample.signals),
        dataset_id=None,
        sample_key=None,
    )
    session.add(recording)
    for signal in sample.signals:
        session.add(
            GroundTruthModel(
                id=f"gt_seed_{STANDALONE_SAMPLE_ID}_{signal.id}",
                recording_id=recording.id,
                t_start_s=signal.t_start_s,
                t_end_s=signal.t_end_s,
                f_low_hz=signal.f_low_hz,
                f_high_hz=signal.f_high_hz,
                class_id=signal.class_id,
                class_name=signal.class_name,
            )
        )
    session.commit()
    return "created", recording.id
