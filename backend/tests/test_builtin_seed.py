"""Built-in delivery data: Mini-SpaceNet dataset + standalone sample.

A delivery machine has no SpaceNet corpus, so the bundled seed_data/ must register
a usable dataset and standalone sample on startup — idempotently and without ever
blocking startup.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.datasets.model import DatasetModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel
from app.seed.builtin import (
    SEED_DATASET_NAME,
    STANDALONE_RECORDING_ID,
    seed_builtin_data,
)


def _write_sample(directory: Path, stem: str, *, class_id: int = 9) -> None:
    # 4,000 bytes -> 1,000 float16 I/Q pairs; observation_range 2400-2500 MHz gives
    # Fs = 100 MHz and a 10 us sample, so the ground-truth window below fits.
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.bin").write_bytes(b"\x00" * 4_000)
    payload = {
        "observation_range": [2400.0, 2500.0],
        "signals": [
            {
                "signal_id": 0,
                "start_frequency": 2410.0,
                "end_frequency": 2420.0,
                "start_time": 0.001,
                "end_time": 0.005,
                "class": class_id,
            }
        ],
    }
    (directory / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")


def _make_seed_data(project_root: Path) -> None:
    root = project_root / "seed_data"
    for stem in ("0", "1", "2"):
        _write_sample(root / "mini-spacenet" / "test", stem)
    _write_sample(root / "standalone" / "test", "3")


def test_seeds_dataset_and_standalone_sample(session, settings):
    _make_seed_data(settings.project_root)

    outcome = seed_builtin_data(
        session, project_root=settings.project_root, label_space_root=settings.label_space_root
    )

    assert outcome.dataset_status == "created"
    dataset = session.get(DatasetModel, outcome.dataset_id)
    assert dataset is not None
    assert dataset.name == SEED_DATASET_NAME
    assert dataset.sample_count == 3
    assert dataset.ground_truth_sample_count == 3

    assert outcome.standalone_status == "created"
    assert outcome.standalone_recording_id == STANDALONE_RECORDING_ID
    recording = session.get(RecordingModel, STANDALONE_RECORDING_ID)
    assert recording is not None
    assert recording.dataset_id is None
    assert recording.sample_key is None
    assert recording.label_space == "spacenet_14"
    assert recording.has_ground_truth is True
    assert recording.data_format == "float16_interleaved_le"
    assert recording.num_samples == 1_000
    gt_count = session.scalar(
        select(func.count()).select_from(GroundTruthModel).where(
            GroundTruthModel.recording_id == STANDALONE_RECORDING_ID
        )
    )
    assert gt_count == 1


def test_seeding_is_idempotent(session, settings):
    _make_seed_data(settings.project_root)

    first = seed_builtin_data(
        session, project_root=settings.project_root, label_space_root=settings.label_space_root
    )
    second = seed_builtin_data(
        session, project_root=settings.project_root, label_space_root=settings.label_space_root
    )

    assert first.changed is True
    assert second.dataset_status == "already_present"
    assert second.standalone_status == "already_present"
    assert second.changed is False
    assert second.dataset_id == first.dataset_id
    assert second.standalone_recording_id == first.standalone_recording_id
    assert session.scalar(select(func.count()).select_from(DatasetModel)) == 1
    assert session.scalar(
        select(func.count()).select_from(RecordingModel).where(RecordingModel.source == "builtin")
    ) == 1
    # The dataset's three members are not duplicated either.
    assert session.scalar(
        select(func.count()).select_from(RecordingModel).where(
            RecordingModel.dataset_id == first.dataset_id
        )
    ) == 3


def test_missing_seed_data_is_reported_not_fatal(session, settings):
    outcome = seed_builtin_data(
        session, project_root=settings.project_root, label_space_root=settings.label_space_root
    )
    assert outcome.dataset_status == "unavailable"
    assert outcome.standalone_status == "unavailable"
    assert outcome.changed is False
