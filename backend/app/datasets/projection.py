"""Deterministic dataset projection over existing Recording metadata.

A dataset projection is a read model over ``RecordingModel`` rows that share a
stable physical/logical dataset identity. It does NOT introduce a persisted
dataset table.

Identity inputs (deterministic, opaque, URL-safe):
    source, dataset_name, dataset_split, label_space, normalized dataset root

``dataset_name`` and ``dataset_split`` are display components only. Two
independently registered dataset roots that share display fields MUST remain two
distinct projections.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.benchmarks.manifest import canonical_json_bytes
from app.core.errors import PlatformError
from app.recordings.model import RecordingModel

PROJECTION_ID_PREFIX = "dsproj_"


def normalize_dataset_root(external_path: str, dataset_split: str) -> str:
    """Derive the normalized dataset root from a member sample's external path.

    SpaceNet member paths have the shape ``<root>/<split>/<sample>.bin``; the
    projection root is the parent of the split directory when the split matches,
    otherwise the sample's immediate parent.
    """
    parent = Path(external_path).parent
    root = parent.parent if parent.name == dataset_split else parent
    return os.path.normcase(os.path.normpath(str(root)))


def compute_dataset_projection_id(
    *,
    source: str,
    dataset_name: str,
    dataset_split: str,
    label_space: str | None,
    normalized_root: str,
) -> str:
    payload = {
        "source": source,
        "dataset_name": dataset_name,
        "dataset_split": dataset_split,
        "label_space": label_space,
        "root": normalized_root,
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{PROJECTION_ID_PREFIX}{digest[:32]}"


@dataclass(frozen=True)
class DatasetProjection:
    dataset_projection_id: str
    source: str
    dataset_name: str
    dataset_split: str
    label_space: str | None
    normalized_root: str
    source_location: str | None


class DatasetProjectionResolver:
    """Single authoritative resolver for ``dataset_projection_id`` membership."""

    def __init__(self, session) -> None:
        self.session = session

    def _rows(self) -> list[RecordingModel]:
        return list(
            self.session.scalars(
                select(RecordingModel).where(RecordingModel.dataset_name.is_not(None))
            ).all()
        )

    def _projection_for_recording(self, recording: RecordingModel) -> DatasetProjection:
        if recording.external_path:
            root = normalize_dataset_root(recording.external_path, recording.dataset_split or "")
            location = str(Path(recording.external_path).parent.parent)
        else:
            root = ""
            location = None
        source = recording.source or "custom"
        projection_id = compute_dataset_projection_id(
            source=source,
            dataset_name=recording.dataset_name or "",
            dataset_split=recording.dataset_split or "",
            label_space=recording.label_space,
            normalized_root=root,
        )
        return DatasetProjection(
            dataset_projection_id=projection_id,
            source=source,
            dataset_name=recording.dataset_name or "",
            dataset_split=recording.dataset_split or "",
            label_space=recording.label_space,
            normalized_root=root,
            source_location=location,
        )

    def _grouped(self) -> dict[str, list[RecordingModel]]:
        grouped: dict[str, list[RecordingModel]] = {}
        for recording in self._rows():
            projection = self._projection_for_recording(recording)
            grouped.setdefault(projection.dataset_projection_id, []).append(recording)
        return grouped

    def list(self, limit: int, offset: int) -> tuple[list[DatasetProjection], int]:
        grouped = self._grouped()
        projections = sorted(
            (self._projection_for_recording(members[0]) for members in grouped.values()),
            key=lambda item: (
                item.dataset_name,
                item.dataset_split,
                item.source,
                item.dataset_projection_id,
            ),
        )
        return projections[offset : offset + limit], len(projections)

    def get(self, dataset_projection_id: str) -> DatasetProjection:
        grouped = self._grouped()
        members = grouped.get(dataset_projection_id)
        if not members:
            raise PlatformError(
                "DATASET_PROJECTION_NOT_FOUND", "Dataset projection was not found.", 404
            )
        return self._projection_for_recording(members[0])

    def members(
        self, dataset_projection_id: str, *, require_ground_truth: bool = False
    ) -> list[RecordingModel]:
        grouped = self._grouped()
        members = grouped.get(dataset_projection_id)
        if members is None:
            raise PlatformError(
                "DATASET_PROJECTION_NOT_FOUND", "Dataset projection was not found.", 404
            )
        ordered = sorted(members, key=lambda recording: (recording.name, recording.id))
        if require_ground_truth:
            ordered = [recording for recording in ordered if recording.has_ground_truth]
        return ordered

    def find_for_recording(self, recording: RecordingModel) -> DatasetProjection | None:
        if recording.dataset_name is None:
            return None
        return self._projection_for_recording(recording)
