"""First-class Dataset Analysis membership authority (P3).

Product invariant:

    Dataset Analysis: Dataset + Pipeline -> per-Sample AnalysisRuns
    Evaluation:       Dataset Analysis + usable GT -> metrics

Dataset Analysis membership is ALL samples of the DatasetModel, regardless of
Ground Truth. ``DatasetModel.dataset_id`` is the authority; the legacy
DatasetProjection path remains only for compatibility and is NOT used here.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.benchmarks.manifest import (
    FrozenRecordingManifest,
    ManifestGroundTruth,
    ManifestRecording,
    build_recording_manifest,
)
from app.core.errors import PlatformError
from app.datasets.model import DatasetModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


@dataclass(frozen=True)
class DatasetAnalysisEntry:
    manifest_order: int
    recording_id: str
    recording_name: str
    gt_count: int


@dataclass(frozen=True)
class DatasetAnalysisManifest:
    dataset_id: str
    dataset_name: str
    dataset_split: str
    label_space: str
    recording_manifest_hash: str
    expected_recordings: int
    entries: tuple[DatasetAnalysisEntry, ...]
    frozen: FrozenRecordingManifest


def load_dataset_analysis_members(session: Session, dataset_id: str) -> list[RecordingModel]:
    """ALL Dataset members, ordered by sample_key then name then id. Never filters GT."""
    return list(
        session.scalars(
            select(RecordingModel)
            .where(RecordingModel.dataset_id == dataset_id)
            .order_by(RecordingModel.sample_key, RecordingModel.name, RecordingModel.id)
        ).all()
    )


def _frozen_manifest_for_dataset(
    session: Session, dataset: DatasetModel, members: list[RecordingModel]
) -> FrozenRecordingManifest:
    label_space = dataset.label_space or ""
    recording_ids = [member.id for member in members]
    gt_by_recording: dict[str, list[GroundTruthModel]] = {}
    if recording_ids:
        rows = list(
            session.scalars(
                select(GroundTruthModel).where(GroundTruthModel.recording_id.in_(recording_ids))
            ).all()
        )
        for row in rows:
            gt_by_recording.setdefault(row.recording_id, []).append(row)

    manifests = [
        ManifestRecording(
            recording_id=member.id,
            name=member.sample_key or member.name,
            data_format=member.data_format,
            sample_rate_hz=member.sample_rate_hz,
            center_frequency_hz=member.center_frequency_hz,
            frequency_low_hz=member.frequency_low_hz,
            frequency_high_hz=member.frequency_high_hz,
            num_samples=member.num_samples,
            duration_s=member.duration_s,
            ground_truth=tuple(
                ManifestGroundTruth(
                    t_start_s=gt.t_start_s, t_end_s=gt.t_end_s,
                    f_low_hz=gt.f_low_hz, f_high_hz=gt.f_high_hz,
                    class_id=gt.class_id, class_name=gt.class_name,
                )
                for gt in sorted(gt_by_recording.get(member.id, []), key=lambda row: row.id)
            ),
        )
        for member in members
    ]
    try:
        return build_recording_manifest(dataset.name, dataset.split, label_space, manifests)
    except ValueError as error:
        raise PlatformError(
            "DATASET_ANALYSIS_MANIFEST_INVALID",
            f"Dataset analysis manifest is not well-formed: {error}",
            422,
        ) from error


def build_dataset_analysis_manifest(session: Session, dataset_id: str) -> DatasetAnalysisManifest:
    dataset = session.get(DatasetModel, dataset_id)
    if dataset is None:
        raise PlatformError("DATASET_NOT_FOUND", "Dataset was not found.", 404)
    members = load_dataset_analysis_members(session, dataset_id)
    if not members:
        raise PlatformError(
            "DATASET_SNAPSHOT_EMPTY", "Dataset has no samples to analyze.", 422
        )
    frozen = _frozen_manifest_for_dataset(session, dataset, members)
    entries = tuple(
        DatasetAnalysisEntry(
            manifest_order=index,
            recording_id=entry.recording_id,
            recording_name=entry.name,
            gt_count=len(entry.ground_truth),
        )
        for index, entry in enumerate(frozen.entries)
    )
    return DatasetAnalysisManifest(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        dataset_split=dataset.split,
        label_space=dataset.label_space or "",
        recording_manifest_hash=frozen.sha256,
        expected_recordings=len(entries),
        entries=entries,
        frozen=frozen,
    )
