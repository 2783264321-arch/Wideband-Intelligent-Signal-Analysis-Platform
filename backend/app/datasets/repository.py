"""Persistence helpers for first-class DatasetModel authority (P1)."""
from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasets.identity import fingerprint_for_recordings
from app.datasets.model import DatasetModel


def new_dataset_id() -> str:
    return f"ds_{uuid4().hex}"


def find_dataset(
    session: Session,
    *,
    adapter_id: str,
    split: str,
    local_root: str,
    name: str,
) -> DatasetModel | None:
    return session.scalar(
        select(DatasetModel).where(
            DatasetModel.adapter_id == adapter_id,
            DatasetModel.split == split,
            DatasetModel.local_root == local_root,
            DatasetModel.name == name,
        )
    )


def get_or_create_dataset(
    session: Session,
    *,
    adapter_id: str,
    split: str,
    local_root: str,
    name: str,
    label_space: str | None,
) -> DatasetModel:
    dataset = find_dataset(
        session, adapter_id=adapter_id, split=split, local_root=local_root, name=name
    )
    if dataset is not None:
        if dataset.label_space is None and label_space is not None:
            dataset.label_space = label_space
        return dataset
    dataset = DatasetModel(
        id=new_dataset_id(),
        name=name,
        split=split,
        adapter_id=adapter_id,
        label_space=label_space,
        local_root=local_root,
        portable_fingerprint=None,
        sample_count=0,
        ground_truth_sample_count=0,
    )
    session.add(dataset)
    session.flush()
    return dataset


def refresh_dataset_stats(
    session: Session, dataset: DatasetModel, recordings: Sequence
) -> None:
    """Recompute counts and the portable fingerprint from current membership."""
    dataset.sample_count = len(recordings)
    dataset.ground_truth_sample_count = sum(
        1 for recording in recordings if recording.has_ground_truth
    )
    dataset.portable_fingerprint = fingerprint_for_recordings(
        session,
        name=dataset.name,
        split=dataset.split,
        label_space=dataset.label_space,
        recordings=recordings,
    )
