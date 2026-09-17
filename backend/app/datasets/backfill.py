"""Legacy dataset backfill (P1).

Existing databases contain dataset-member Recordings with only the legacy
``dataset_name`` / ``dataset_split`` / ``external_path`` metadata. This backfill
uses the existing deterministic projection grouping as the SOURCE and persists
one DatasetModel per local dataset group, then links members via
``dataset_id`` / ``sample_key``.

It never copies raw IQ, never rewrites ``external_path``, and never deletes
projection metadata. It is idempotent and skips groups already linked.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.datasets.projection import DatasetProjection, DatasetProjectionResolver
from app.datasets.repository import find_dataset, get_or_create_dataset, refresh_dataset_stats
from app.recordings.model import RecordingModel


def backfill_legacy_datasets(session: Session) -> int:
    """Backfill legacy dataset members. Returns the number of groups processed."""
    resolver = DatasetProjectionResolver(session)
    processed = 0
    for members in resolver.grouped().values():
        if not members:
            continue
        projection = resolver.find_for_recording(members[0])
        if projection is None:
            continue
        dataset = find_dataset(
            session,
            adapter_id=projection.source,
            split=projection.dataset_split,
            local_root=projection.normalized_root,
            name=projection.dataset_name,
        )
        already_linked = dataset is not None and all(
            member.dataset_id == dataset.id and member.sample_key for member in members
        )
        if already_linked and dataset is not None and dataset.portable_fingerprint is not None:
            continue
        dataset = _persist_group(session, projection, members, dataset)
        processed += 1
    session.flush()
    return processed


def _persist_group(
    session: Session,
    projection: DatasetProjection,
    members: list[RecordingModel],
    dataset,
):
    dataset = dataset or get_or_create_dataset(
        session,
        adapter_id=projection.source,
        split=projection.dataset_split,
        local_root=projection.normalized_root,
        name=projection.dataset_name,
        label_space=projection.label_space,
    )
    for member in members:
        member.dataset_id = dataset.id
        if not member.sample_key:
            member.sample_key = member.name
        # Keep legacy display fields consistent during the transition.
        if member.dataset_name is None:
            member.dataset_name = projection.dataset_name
        if member.dataset_split is None:
            member.dataset_split = projection.dataset_split
        if member.label_space is None:
            member.label_space = projection.label_space
    session.flush()
    refresh_dataset_stats(session, dataset, members)
    return dataset
